# risk_guard.py — Drawdown Sentinel: melden, reagieren, verbessern
# Laeuft parallel zur Bridge & bot.py. Keine Boersen-Keys noetig.

import json
import time
from pathlib import Path
from datetime import datetime

ROOT = Path.cwd()
BRIDGE_OUT = ROOT / "bridge_out"
REPORTS = BRIDGE_OUT / "reports"
SNAPSHOTS = BRIDGE_OUT / "snapshots"
CONTROL = BRIDGE_OUT / "control"
CONTROL.mkdir(parents=True, exist_ok=True)

STATUS_PATH = REPORTS / "status.json"

# --------- Defaults + optionales Nachladen aus config (ohne E402) ----------
MAX_DD_LIMIT = 8.0  # %
DAY_LOSS_LIMIT = -3.0  # %


def _refresh_limits():
    """Optional: aus config/config.yaml nachladen; faellt sonst auf Defaults zurueck."""
    global MAX_DD_LIMIT, DAY_LOSS_LIMIT
    try:
        from config_loader import load_config  # lokal importiert -> keine E402

        cfg = load_config()
        MAX_DD_LIMIT = float(cfg.risk.max_drawdown_pct)
        DAY_LOSS_LIMIT = float(cfg.risk.day_loss_limit_pct)
    except Exception:
        pass


# ---------------------------------------------------------------------------


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
    # ohne unrealized PnL schaetzen wir „schlimmste“ ueber Notional
    if not snapshot or "exposures" not in snapshot:
        return []
    exposures = snapshot["exposures"]
    exposures = sorted(exposures, key=lambda e: float(e.get("notional_eur", 0.0)), reverse=True)
    syms = []
    for e in exposures[:topn]:
        syms.append(e.get("symbol"))
    return syms


def write_control(pause: bool, to_close_symbols, tuning):
    mode = {"pause_new_signals": bool(pause), "ts": int(time.time() * 1000)}
    with open(CONTROL / "mode.json", "w", encoding="utf-8") as f:
        json.dump(mode, f, ensure_ascii=False, indent=2)

    if to_close_symbols:
        with open(CONTROL / "force_close.json", "w", encoding="utf-8") as f:
            json.dump(
                {"symbols": to_close_symbols, "ts": int(time.time() * 1000)},
                f,
                ensure_ascii=False,
                indent=2,
            )

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
    fn = REPORTS / f"guard_alert_{alert['ts']}.json"
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(alert, f, ensure_ascii=False, indent=2)
    print("[guard] ALERT:", kind, "->", fn)


def propose_tuning(status):
    return {
        "risk_per_trade_pct": 0.005,  # 0.5%
        "atr_stop_mult": 1.6,
        "atr_tp_mult": 2.2,
        "max_concurrent_positions": 2,
    }


def main():
    # nur EINMAL Limits aus config ziehen (falls vorhanden), sonst Defaults behalten
    _refresh_limits()
    print("[guard] running... watching", STATUS_PATH)
    last_seen_ts = None

    while True:
        st = load_json(STATUS_PATH)
        if st and st.get("last_update_ts") != last_seen_ts:
            last_seen_ts = st.get("last_update_ts")

            _ = float(
                st.get("winrate_pct", 0.0)
            )  # Platzhalter – day PnL waere besser via risk events
            max_dd = float(st.get("max_drawdown_pct", 0.0))

            snap = latest_snapshot()  # fuer exposures
            status = {
                "paper_capital": st.get("paper_capital"),
                "realized_pnl": st.get("realized_pnl"),
                "profit_factor": st.get("profit_factor"),
                "winrate_pct": st.get("winrate_pct"),
                "max_drawdown_pct": max_dd,
            }

            trigger = None
            if max_dd >= MAX_DD_LIMIT:
                trigger = "MAX_DRAWDOWN_EXCEEDED"
            # Beispiel fuer DAY_LOSS_LIMIT koennte hier spaeter ergaenzt werden

            if trigger:
                offenders = worst_offenders(snap, topn=2)
                tuning = propose_tuning(status)
                write_alert_report(trigger, status, offenders, tuning)
                write_control(True, offenders, tuning)
            else:
                # alles ok -> ggf. Pause sanft aufheben
                write_control(False, [], None)

        time.sleep(15)  # alle 15s pruefen


if __name__ == "__main__":
    main()
