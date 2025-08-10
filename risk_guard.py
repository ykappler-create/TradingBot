# risk_guard.py — Drawdown Sentinel: melden, reagieren, verbessern
# Läuft parallel zur Bridge & bot.py. Keine Börsen-Keys nötig.

import os
import json
import time
from pathlib import Path
from datetime import datetime

ROOT = Path.cwd()
BRIDGE_OUT = Path(os.getenv("BRIDGE_OUT", "bridge_out"))
REPORTS = BRIDGE_OUT / "reports"
SNAPSHOTS = BRIDGE_OUT / "snapshots"
CONTROL = BRIDGE_OUT / "control"
CONTROL.mkdir(parents=True, exist_ok=True)

STATUS_PATH = REPORTS / "status.json"

# Schwellen aus deiner Policy
MAX_DD_LIMIT = 8.0  # %
DAY_LOSS_LIMIT = -3.0  # %


def load_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def latest_snapshot():
    snaps = sorted(SNAPSHOTS.glob("positions_*.json"))
    if not snaps:
        return None
    return load_json(snaps[-1])


def worst_offenders(snapshot, topn=2):
    # ohne unrealized PnL schätzen wir „schlimmste“ über Notional
    if not snapshot or "exposures" not in snapshot:
        return []
    exposures = snapshot["exposures"]
    exposures = sorted(exposures, key=lambda e: float(e.get("notional_eur", 0.0)), reverse=True)
    syms = []
    for e in exposures[:topn]:
        syms.append(e.get("symbol"))
    return syms


def write_control(pause: bool, to_close_symbols, tuning):
    # mode.json: globale Flags (Pause neuer Signale)
    mode = {"pause_new_signals": bool(pause), "ts": int(time.time() * 1000)}
    with open(CONTROL / "mode.json", "w", encoding="utf-8") as f:
        json.dump(mode, f, ensure_ascii=False, indent=2)

    # force_close.json: Liste von Symbolen, die sofort geschlossen werden sollen
    if to_close_symbols:
        with open(CONTROL / "force_close.json", "w", encoding="utf-8") as f:
            json.dump(
                {"symbols": to_close_symbols, "ts": int(time.time() * 1000)},
                f,
                ensure_ascii=False,
                indent=2,
            )

    # tuning.json: neue, konservativere Parameter
    if tuning:
        with open(CONTROL / "tuning.json", "w", encoding="utf-8") as f:
            json.dump(tuning, f, ensure_ascii=False, indent=2)


def write_alert_report(kind, status, offenders, tuning):
    alert = {
        "ts": int(time.time() * 1000),
        "when": datetime.utcnow().isoformat() + "Z",
        "kind": kind,
        "status": status,
        "offenders": offenders,
        "tuning": tuning,
    }
    fn = REPORTS / f"alert_{alert['ts']}.json"
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(alert, f, ensure_ascii=False, indent=2)
    print("[guard] ALERT:", kind, "->", fn)


def propose_tuning(status):
    # Vorschlag: Risiko runter, Stop breiter, weniger gleichzeitige Positionen
    return {
        "risk_per_trade_pct": 0.005,  # 0.5%
        "atr_stop_mult": 1.6,  # breiterer Stop
        "atr_tp_mult": 2.2,
        "max_concurrent_positions": 2,
    }


def main():
    print("[guard] running… watching", STATUS_PATH)
    last_seen_ts = None

    while True:
        st = load_json(STATUS_PATH)
        if st and st.get("last_update_ts") != last_seen_ts:
            last_seen_ts = st.get("last_update_ts")

            _ = float(
                st.get("winrate_pct", 0.0)
            )  # Platzhalter – day PnL kommt besser über risk events
            max_dd = float(st.get("max_drawdown_pct", 0.0))

            # Besser: letzter Risk-Event auswerten (day_pnl_pct, rolling_dd_pct)
            snap = latest_snapshot()  # für exposures
            status = {
                "paper_capital": st.get("paper_capital"),
                "realized_pnl": st.get("realized_pnl"),
                "profit_factor": st.get("profit_factor"),
                "winrate_pct": st.get("winrate_pct"),
                "max_drawdown_pct": max_dd,
            }

            # Entscheide anhand MaxDD aus status.json UND (falls vorhanden) rolling_dd/day via guard-eigenen Logik
            trigger = None
            if max_dd > MAX_DD_LIMIT:
                trigger = "ROLLING_MAX_DRAWDOWN_EXCEEDED"

            # Fallback: wenn kein daily-PnL da, nutzen wir nur MaxDD-Trigger
            # (Dein bot.py schreibt day_pnl_pct im risk-Log, das kann man optional hier zusätzlich einlesen)

            if trigger:
                offenders = worst_offenders(snap, topn=2)
                tuning = propose_tuning(status)
                write_control(True, offenders, tuning)
                write_alert_report(trigger, status, offenders, tuning)
            else:
                # falls alles okay, hebe ggf. Pause auf (sanft)
                write_control(False, [], None)

        time.sleep(15)  # alle 15s prüfen


if __name__ == "__main__":
    main()
