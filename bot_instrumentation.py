# bot_instrumentation.py
import json, os, time, uuid
from pathlib import Path
from typing import Optional, Dict, Any, List

BRIDGE_OUT = Path(os.getenv("BRIDGE_OUT", "bridge_out"))
EVENTS_DIR = BRIDGE_OUT / "events"
SNAP_DIR = BRIDGE_OUT / "snapshots"
EVENTS_DIR.mkdir(parents=True, exist_ok=True)
(SNAP_DIR).mkdir(parents=True, exist_ok=True)
(EVENTS_DIR / "trades").mkdir(parents=True, exist_ok=True)
(EVENTS_DIR / "risk").mkdir(parents=True, exist_ok=True)
(EVENTS_DIR / "equity").mkdir(parents=True, exist_ok=True)

PAPER_CAPITAL = float(os.getenv("PAPER_CAPITAL", "10000"))

def _now_ms() -> int:
    return int(time.time() * 1000)

def _write_event(kind: str, payload: Dict[str, Any]) -> Path:
    ts = _now_ms()
    fn = EVENTS_DIR / kind / f"{ts}_{uuid.uuid4().hex}.json"
    payload = {"ts": ts, "type": kind, **payload}
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return fn

# --- Trades ---

def log_trade_open(symbol: str, side: str, qty: float, price: float, leverage: float,
                   exchange: str, strategy_id: str, rationale: str) -> Path:
    return _write_event("trades", {
        "event": "open",
        "symbol": symbol,
        "side": side,             # "long" | "short"
        "qty": qty,
        "price": price,
        "leverage": leverage,
        "exchange": exchange,     # "alpaca" | "bitget"
        "strategy_id": strategy_id,
        "rationale": rationale,
    })


def log_trade_close(order_ref: str, symbol: str, exit_price: float, profit: float,
                    pnl_pct: float, fees: float = 0.0) -> Path:
    return _write_event("trades", {
        "event": "close",
        "order_ref": order_ref,
        "symbol": symbol,
        "exit_price": exit_price,
        "profit": profit,
        "pnl_pct": pnl_pct,
        "fees": fees,
    })

# --- Equity & Risiko ---

def log_equity(equity: float) -> Path:
    return _write_event("equity", {
        "equity": equity
    })


def log_risk(open_risk_pct: float, day_pnl_pct: float, rolling_dd_pct: float,
             mode: str = "normal") -> Path:
    return _write_event("risk", {
        "open_risk_pct": open_risk_pct,
        "day_pnl_pct": day_pnl_pct,
        "rolling_dd_pct": rolling_dd_pct,
        "mode": mode,  # normal | defensive | ultra_defensive
    })

# --- Snapshots (z. B. Exposure je Asset) ---

def log_position_snapshot(exposures: List[Dict[str, Any]], avg_leverage: float) -> Path:
    fn = SNAP_DIR / f"positions_{_now_ms()}_{uuid.uuid4().hex}.json"
    snap = {
        "ts": _now_ms(),
        "exposures": exposures,  # [ {symbol, direction, notional_eur, risk_pct} ]
        "avg_leverage": avg_leverage,
        "paper_capital": PAPER_CAPITAL,
    }
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    return fn