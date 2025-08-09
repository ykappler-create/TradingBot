# dashboard.py — Live Performance Monitor mit 14-Tage Gate (Streamlit 1.48+)
# - Liest Bridge-Dateien aus bridge_out/
# - 14-Tage-Kennzahlen aus Trade-CLOSE-Events: Net PnL, Profit Factor, Max Drawdown
# - Ampel (ROT/GRÜN) für die Gate-Bedingung:
#     Net PnL > 0  AND  Profit Factor > 1.2  AND  Max DD < 8%
# - Button: „Echtgeld-Anfrage vorbereiten“ → schreibt eine gate_request-JSON
#
# Start:  streamlit run dashboard.py
# Deps:   pip install streamlit plotly pandas numpy

import os, json, time, math
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple

import pandas as pd
import numpy as np
import plotly.graph_objs as go
import streamlit as st

# -----------------------------
# Settings / Pfade
# -----------------------------
st.set_page_config(page_title="TradingBot – Live Performance", layout="wide")
BRIDGE_OUT = Path(os.getenv("BRIDGE_OUT", "bridge_out"))
REPORTS = BRIDGE_OUT / "reports"
EVENTS  = BRIDGE_OUT / "events"
SNAPS   = BRIDGE_OUT / "snapshots"
CONTROL = BRIDGE_OUT / "control"

for p in [REPORTS, EVENTS, SNAPS, CONTROL]:
    p.mkdir(parents=True, exist_ok=True)

REFRESH_SECS = 10
GATE_PF = 1.2
GATE_MAX_DD = 8.0

# -----------------------------
# Helper
# -----------------------------
def load_json(path: Path) -> Dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def ts2dt(ts_ms) -> str:
    try:
        return datetime.utcfromtimestamp(int(ts_ms)/1000.0).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"

def latest_status() -> Dict[str, Any]:
    return load_json(REPORTS / "status.json") or {}

def equity_curve_df() -> pd.DataFrame:
    csv = REPORTS / "equity_curve.csv"
    if not csv.exists():
        return pd.DataFrame(columns=["ts_ms","equity"])
    try:
        df = pd.read_csv(csv)
        if not {"ts_ms","equity"}.issubset(df.columns):
            return pd.DataFrame(columns=["ts_ms","equity"])
        return df
    except Exception:
        return pd.DataFrame(columns=["ts_ms","equity"])

def latest_snapshot() -> Dict[str, Any]:
    snaps = sorted(SNAPS.glob("positions_*.json"))
    if not snaps:
        return {}
    return load_json(snaps[-1]) or {}

def read_trade_events(limit: int = 5000) -> List[Dict[str, Any]]:
    trade_dir = EVENTS / "trades"
    if not trade_dir.exists():
        return []
    files = sorted(trade_dir.glob("*.json"))
    rows: List[Dict[str, Any]] = []
    for fn in files[-limit:]:
        try:
            rows.append(json.loads(fn.read_text(encoding="utf-8")))
        except Exception:
            pass
    return rows

def compute_drawdown_from_pnls(pnls: List[float], start_equity: float) -> float:
    """Cumulative equity from start_equity + pnls; return Max DD % in window."""
    equity = start_equity
    peak = start_equity
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return float(max_dd)

def compute_profit_factor(pnls: List[float]) -> float:
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    if not wins and not losses:
        return 0.0
    if sum(losses) == 0:
        return float(sum(wins) / 1e-9)
    return float(sum(wins) / sum(losses))

def filter_close_pnls_last_days(trades: List[Dict[str, Any]], days: int) -> Tuple[List[float], List[Dict[str, Any]]]:
    cutoff = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    pnls: List[float] = []
    closes: List[Dict[str, Any]] = []
    for t in trades:
        if t.get("event") == "close":
            ts = int(t.get("ts", 0))
            if ts >= cutoff:
                p = float(t.get("profit", 0.0)) - float(t.get("fees", 0.0))
                pnls.append(p)
                closes.append(t)
    return pnls, closes

# -----------------------------
# Sidebar / Header
# -----------------------------
st.sidebar.title("⚙️ Live Performance")
st.sidebar.write(f"Quelle: `{BRIDGE_OUT}`")
st.sidebar.caption(f"Aktualisiert automatisch alle {REFRESH_SECS} Sekunden")
if st.sidebar.button("Jetzt aktualisieren", use_container_width=True):
    st.rerun()

status = latest_status()
paper_capital  = float(status.get("paper_capital", 10000.0))
realized_pnl   = float(status.get("realized_pnl", 0.0))
profit_factor  = float(status.get("profit_factor", 0.0))
winrate_pct    = float(status.get("winrate_pct", 0.0))
max_dd_pct     = float(status.get("max_drawdown_pct", 0.0))
last_update_ts = status.get("last_update_ts")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Startkapital (Paper)", f"{paper_capital:,.2f} €")
c2.metric("Realized PnL (seit Start)", f"{realized_pnl:,.2f} €")
c3.metric("Profit Factor (seit Start)", f"{profit_factor:.2f}")
c4.metric("Winrate % (seit Start)", f"{winrate_pct:.2f}")
c5.metric("Max Drawdown % (seit Start)", f"{max_dd_pct:.2f}")
st.caption(f"Letztes Bridge-Update: {ts2dt(last_update_ts)} UTC")

st.markdown("---")

# -----------------------------
# Equity-Kurve (seit Start)
# -----------------------------
st.subheader("📈 Equity-Kurve (seit Start)")
eq = equity_curve_df()
if not eq.empty:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pd.to_datetime(eq["ts_ms"], unit="ms"),
        y=eq["equity"],
        mode="lines",
        name="Equity"
    ))
    fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), height=300)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Noch keine Equity-Punkte vorhanden.")

# -----------------------------
# 14-Tage Kennzahlen (aus CLOSE-Events)
# -----------------------------
st.subheader("🧪 14-Tage Kennzahlen (Trade-Close-Events)")
trades = read_trade_events(limit=5000)
pnls_14d, recent_closes = filter_close_pnls_last_days(trades, days=14)

if pnls_14d:
    net_pnl_14d = float(sum(pnls_14d))
    pf_14d = compute_profit_factor(pnls_14d)
    max_dd_14d = compute_drawdown_from_pnls(pnls_14d, paper_capital)

    k1, k2, k3 = st.columns(3)
    k1.metric("Net PnL (14d)", f"{net_pnl_14d:,.2f} €")
    k2.metric("Profit Factor (14d)", f"{pf_14d:.2f}")
    k3.metric("Max Drawdown (14d)", f"{max_dd_14d:.2f}%")

    gate_ok = (net_pnl_14d > 0.0) and (pf_14d > GATE_PF) and (max_dd_14d < GATE_MAX_DD)

    st.markdown("### ✅ 14-Day Gate")
    if gate_ok:
        st.success(f"Gate erfüllt ✅  (PnL>0, PF>{GATE_PF}, DD<{GATE_MAX_DD}%)")
        # Button: Anfrage schreiben
        if st.button("Echtgeld-Anfrage vorbereiten (Coinbase/Kraken/Bitget/Trade Republic)"):
            payload = {
                "ts": int(time.time()*1000),
                "when": datetime.utcnow().isoformat()+"Z",
                "gate": {"net_pnl_14d": net_pnl_14d, "profit_factor_14d": pf_14d, "max_dd_14d": max_dd_14d},
                "request": "APPROVAL_FOR_REAL_MONEY_CONNECTION",
                "notes": "Bitte bestätigen, welche Börse(n) in DE verbunden werden sollen."
            }
            out = REPORTS / f"gate_request_{payload['ts']}.json"
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            st.success(f"Anfrage geschrieben: {out}")
    else:
        reasons = []
        if not (net_pnl_14d > 0.0): reasons.append("Net PnL ≤ 0")
        if not (pf_14d > GATE_PF): reasons.append(f"Profit Factor ≤ {GATE_PF}")
        if not (max_dd_14d < GATE_MAX_DD): reasons.append(f"Max DD ≥ {GATE_MAX_DD}%")
        st.error("Gate **nicht** erfüllt ❌ – " + ", ".join(reasons))
        st.caption("Der Bot läuft weiter im Paper-Mode. Optimierung/Feintuning empfohlen.")

else:
    st.info("Für die letzten 14 Tage liegen noch keine CLOSE-Events vor (oder zu wenige).")

# -----------------------------
# Offene Positionen (Snapshot)
# -----------------------------
st.markdown("---")
st.subheader("📦 Offene Positionen (letzter Snapshot)")
snap = latest_snapshot()
pos_df = pd.DataFrame(snap.get("exposures", []))
if not pos_df.empty:
    pos_df = pos_df.sort_values("notional_eur", ascending=False)
    st.dataframe(pos_df, use_container_width=True)
    if {"symbol","notional_eur"}.issubset(pos_df.columns):
        exp_fig = go.Figure()
        exp_fig.add_trace(go.Bar(
            x=pos_df["symbol"].astype(str),
            y=pos_df["notional_eur"].astype(float),
            name="Exposure €"
        ))
        exp_fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), height=260)
        st.plotly_chart(exp_fig, use_container_width=True)
else:
    st.info("Keine offenen Positionen erkannt.")

# -----------------------------
# Letzte Trades (Open/Close + Rationale)
# -----------------------------
st.subheader("🧾 Letzte Trades")
if trades:
    view_rows: List[Dict[str, Any]] = []
    for t in trades[-120:]:
        evt = t.get("event")
        symbol = t.get("symbol")
        ts = t.get("ts")
        if evt == "open":
            view_rows.append({
                "Zeit (UTC)": ts2dt(ts),
                "Event": "OPEN",
                "Symbol": symbol,
                "Preis/Exit": t.get("price"),
                "Qty": t.get("qty"),
                "Exchange": t.get("exchange"),
                "Strategie": t.get("strategy_id"),
                "Rationale": t.get("rationale", ""),
            })
        elif evt == "close":
            view_rows.append({
                "Zeit (UTC)": ts2dt(ts),
                "Event": "CLOSE",
                "Symbol": symbol,
                "Preis/Exit": t.get("exit_price"),
                "PnL €": t.get("profit"),
                "PnL %": t.get("pnl_pct"),
                "Fees": t.get("fees", 0.0),
            })
    tdf = pd.DataFrame(view_rows)
    st.dataframe(tdf, use_container_width=True)
else:
    st.info("Noch keine Trade-Events vorhanden.")

# -----------------------------
# Auto-Refresh
# -----------------------------
time.sleep(REFRESH_SECS)
st.rerun()
