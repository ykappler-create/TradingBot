# bot.py — Final All-in-One: Live-Signale (SMA/ATR), integrierter Risikowächter,
# Paper-Logging für Bridge, Bitget + Alpaca, Endlosschleife.
# Keine echten Orders. Läuft, bis du stoppst (CTRL+C).

import os
import time
import json
import random
import traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests
import numpy as np
import pandas as pd
from dotenv import load_dotenv

# =============================
# Bridge-Logger: bevorzugt bot_instrumentation, sonst Fallback
# =============================
BRIDGE_OUT = Path(os.getenv("BRIDGE_OUT", "bridge_out"))
EVENTS_DIR = BRIDGE_OUT / "events"
REPORTS_DIR = BRIDGE_OUT / "reports"
SNAP_DIR = BRIDGE_OUT / "snapshots"
for p in [EVENTS_DIR / "trades", EVENTS_DIR / "risk", EVENTS_DIR / "equity", REPORTS_DIR, SNAP_DIR]:
    p.mkdir(parents=True, exist_ok=True)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _write_event(dirpath: Path, payload: dict) -> Path:
    fn = dirpath / f"{_now_ms()}_{random.randrange(1,1_000_000):06d}.json"
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return fn


# Versuche offiziellen Logger zu importieren
try:
    from bot_instrumentation import (
        log_trade_open as _log_trade_open,
        log_trade_close as _log_trade_close,
        log_equity as _log_equity,
        log_risk as _log_risk,
        log_position_snapshot as _log_position_snapshot,
    )

    def log_trade_open(**kw):
        return _log_trade_open(**kw)

    def log_trade_close(**kw):
        return _log_trade_close(**kw)

    def log_equity(v: float):
        return _log_equity(v)

    def log_risk(**kw):
        return _log_risk(**kw)

    def log_position_snapshot(exposures, avg_leverage):
        return _log_position_snapshot(exposures, avg_leverage)

except Exception:
    # Fallback-kompatibel zur Bridge
    def log_trade_open(symbol, side, qty, price, leverage, exchange, strategy_id, rationale):
        return _write_event(
            EVENTS_DIR / "trades",
            {
                "ts": _now_ms(),
                "type": "trades",
                "event": "open",
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "leverage": leverage,
                "exchange": exchange,
                "strategy_id": strategy_id,
                "rationale": rationale,
            },
        )

    def log_trade_close(order_ref, symbol, exit_price, profit, pnl_pct, fees=0.0):
        return _write_event(
            EVENTS_DIR / "trades",
            {
                "ts": _now_ms(),
                "type": "trades",
                "event": "close",
                "order_ref": order_ref,
                "symbol": symbol,
                "exit_price": exit_price,
                "profit": profit,
                "pnl_pct": pnl_pct,
                "fees": fees,
            },
        )

    def log_equity(equity: float):
        return _write_event(
            EVENTS_DIR / "equity", {"ts": _now_ms(), "type": "equity", "equity": equity}
        )

    def log_risk(
        open_risk_pct: float, day_pnl_pct: float, rolling_dd_pct: float, mode: str = "normal"
    ):
        return _write_event(
            EVENTS_DIR / "risk",
            {
                "ts": _now_ms(),
                "type": "risk",
                "open_risk_pct": open_risk_pct,
                "day_pnl_pct": day_pnl_pct,
                "rolling_dd_pct": rolling_dd_pct,
                "mode": mode,
            },
        )

    def log_position_snapshot(exposures, avg_leverage):
        fn = SNAP_DIR / f"positions_{_now_ms()}_{random.randrange(1,1_000_000):06d}.json"
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "ts": _now_ms(),
                    "exposures": exposures,
                    "avg_leverage": avg_leverage,
                    "paper_capital": float(os.getenv("PAPER_CAPITAL", "10000")),
                },
                f,
                ensure_ascii=False,
            )
        return fn


# =============================
# ENV / Konfiguration
# =============================
load_dotenv()
PAPER_CAPITAL = float(os.getenv("PAPER_CAPITAL", "10000"))

# Alpaca optional (Paper-Keys)
ALPACA_KEY = os.getenv("ALPACA_API_KEY_ID")
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET_KEY")
_ALPACA_AVAILABLE = False
try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.requests import StockBarsRequest

    _ALPACA_AVAILABLE = bool(ALPACA_KEY and ALPACA_SECRET)
except Exception:
    _ALPACA_AVAILABLE = False

# Handelssymbole & Strategie-Parameter
CFG = {
    "symbols": {"bitget": ["BTCUSDT"], "alpaca": ["SPY"]},
    "timeframe": "1m",
    "hist_bars": 200,
    "sma_fast": 9,
    "sma_slow": 21,
    "atr_len": 14,
    "atr_stop_mult": 1.2,
    "atr_tp_mult": 2.0,
    "breakeven_rr": 1.0,
    "risk_per_trade_pct": 0.0075,  # 0.75%
    "max_concurrent_positions": 4,
}

# Risikowächter-Schwellen
MAX_DD_LIMIT = 8.0  # %
DAY_LOSS_LIMIT = -3.0  # %


# =============================
# Indikator-Helfer
# =============================
def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(window=n, min_periods=n).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(
        axis=1
    )
    return tr.rolling(window=n, min_periods=n).mean()


# =============================
# Datenquellen
# =============================
BITGET_CANDLES_ENDPOINTS = [
    "https://api.bitget.com/api/spot/v1/market/candles",
    "https://api.bitget.com/api/mix/v1/market/candles",
]


def get_bitget_candles(
    symbol: str, granularity_sec: int = 60, limit: int = 200
) -> Optional[pd.DataFrame]:
    params = {"symbol": symbol, "granularity": granularity_sec}
    for url in BITGET_CANDLES_ENDPOINTS:
        try:
            r = requests.get(url, params=params, timeout=6)
            if r.status_code != 200:
                continue
            data = r.json().get("data")
            if not data:
                continue
            rows = []
            for row in data:
                try:
                    ts_raw = float(row[0])
                    ts = int(ts_raw / 1000) if ts_raw > 1e10 else int(ts_raw)
                    o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
                    rows.append((ts, o, h, l, c))
                except Exception:
                    continue
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).sort_values(
                "ts"
            )
            if len(df) > limit:
                df = df.iloc[-limit:]
            return df
        except Exception:
            continue
    return None


def get_alpaca_bars(symbol: str, limit: int = 200) -> Optional[pd.DataFrame]:
    if not _ALPACA_AVAILABLE:
        return None
    try:
        client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
        end_dt = datetime.utcnow().replace(tzinfo=timezone.utc)
        start_dt = end_dt - timedelta(minutes=limit * 3)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=start_dt,
            end=end_dt,
            limit=limit,
        )
        bars = client.get_stock_bars(req)
        if symbol not in bars:
            return None
        recs = []
        for bar in bars[symbol]:
            ts = int(bar.timestamp.timestamp())
            recs.append((ts, float(bar.open), float(bar.high), float(bar.low), float(bar.close)))
        if not recs:
            return None
        df = pd.DataFrame(recs, columns=["ts", "open", "high", "low", "close"]).sort_values("ts")
        if len(df) > limit:
            df = df.iloc[-limit:]
        return df
    except Exception:
        return None


# =============================
# Signale
# =============================
def signal_from_bars(df: pd.DataFrame, fast: int, slow: int) -> Optional[str]:
    if df is None or len(df) < max(fast, slow) + 2:
        return None
    closes = pd.Series(df["close"].values)
    f = sma(closes, fast)
    s = sma(closes, slow)
    if f.isna().any() or s.isna().any():
        return None
    f_prev, f_curr = f.iloc[-2], f.iloc[-1]
    s_prev, s_curr = s.iloc[-2], s.iloc[-1]
    if f_prev <= s_prev and f_curr > s_curr:
        return "long"
    if f_prev >= s_prev and f_curr < s_curr:
        return "short"
    return None


def last_atr(df: pd.DataFrame, n: int) -> Optional[float]:
    if df is None or len(df) < n + 1:
        return None
    a = atr(pd.Series(df["high"]), pd.Series(df["low"]), pd.Series(df["close"]), n).iloc[-1]
    return None if np.isnan(a) else float(a)


# =============================
# Position & Portfolio
# =============================
class Position:
    def __init__(
        self,
        symbol: str,
        venue: str,
        side: str,
        entry: float,
        atr_val: float,
        qty: float,
        rationale: str,
    ):
        self.symbol = symbol
        self.venue = venue
        self.side = side.lower()
        self.entry = float(entry)
        self.qty = float(qty)
        self.atr = float(atr_val)
        self.rationale = rationale
        self.open_ts = _now_ms()
        self.stop = self._initial_stop()
        self.tp = self._initial_tp()
        self.breakeven_armed = False
        log_trade_open(
            symbol=self.symbol,
            side=self.side,
            qty=self.qty,
            price=self.entry,
            leverage=1.0,
            exchange=self.venue,
            strategy_id="sma_cross_atr",
            rationale=self.rationale,
        )

    def _initial_stop(self) -> float:
        m = CFG["atr_stop_mult"]
        return self.entry - m * self.atr if self.side == "long" else self.entry + m * self.atr

    def _initial_tp(self) -> float:
        m = CFG["atr_tp_mult"]
        return self.entry + m * self.atr if self.side == "long" else self.entry - m * self.atr

    def mtm(self, px: float) -> float:
        return (px - self.entry) * self.qty if self.side == "long" else (self.entry - px) * self.qty

    def rr(self, px: float) -> float:
        risk = CFG["atr_stop_mult"] * self.atr
        if risk <= 0:
            return 0.0
        move = (px - self.entry) if self.side == "long" else (self.entry - px)
        return move / risk

    def arm_breakeven_if_ready(self, px: float):
        if not self.breakeven_armed and self.rr(px) >= CFG["breakeven_rr"]:
            self.stop = self.entry * (1.0002 if self.side == "long" else 0.9998)
            self.breakeven_armed = True

    def exit_reason(self, px: float) -> Optional[Tuple[str, float]]:
        if self.side == "long":
            if px <= self.stop:
                return ("stop", self.stop)
            if px >= self.tp:
                return ("tp", self.tp)
        else:
            if px >= self.stop:
                return ("stop", self.stop)
            if px <= self.tp:
                return ("tp", self.tp)
        return None


class Portfolio:
    def __init__(self, start_eq: float):
        self.equity = float(start_eq)
        self.peak = float(start_eq)
        self.max_dd = 0.0
        self.day_start_date = datetime.utcnow().date()
        self.day_start_eq = float(start_eq)
        self.positions: Dict[str, Position] = {}  # key = venue:symbol

    def key(self, venue, symbol):
        return f"{venue}:{symbol}"

    def day_pnl_pct(self) -> float:
        base = max(1e-9, self.day_start_eq)
        return (self.equity - base) / base * 100.0

    def mark_to_market(self, prices: Dict[str, float]):
        total = 0.0
        for k, pos in self.positions.items():
            px = prices.get(k)
            if px is None:
                continue
            total += pos.mtm(px)
        self.equity = PAPER_CAPITAL + total
        self.peak = max(self.peak, self.equity)
        if self.peak > 0:
            dd = (self.peak - self.equity) / self.peak * 100.0
            self.max_dd = max(self.max_dd, dd)
        if datetime.utcnow().date() != self.day_start_date:
            self.day_start_date = datetime.utcnow().date()
            self.day_start_eq = self.equity

    def open_position(self, venue, symbol, side, entry, atr_val, rationale):
        k = self.key(venue, symbol)
        if k in self.positions:
            return
        risk_eur = PAPER_CAPITAL * CFG["risk_per_trade_pct"]
        stop_dist = CFG["atr_stop_mult"] * atr_val
        if stop_dist <= 0:
            return
        qty = max(1e-6, risk_eur / stop_dist)
        if venue == "alpaca":
            qty = max(1.0, round(qty))
        self.positions[k] = Position(symbol, venue, side, entry, atr_val, qty, rationale)

    def maybe_exit(self, venue, symbol, px: float):
        k = self.key(venue, symbol)
        pos = self.positions.get(k)
        if not pos:
            return
        pos.arm_breakeven_if_ready(px)
        ex = pos.exit_reason(px)
        if ex is None:
            return
        reason, exit_px = ex
        profit = pos.mtm(exit_px)
        notional = max(1e-9, pos.entry * pos.qty)
        pnl_pct = (profit / notional) * 100.0
        log_trade_close(
            order_ref=f"{venue}-{symbol}-{int(time.time())}",
            symbol=symbol,
            exit_price=float(exit_px),
            profit=float(profit),
            pnl_pct=float(pnl_pct),
            fees=0.0,
        )
        del self.positions[k]

    def snapshot_logs(self, prices: Dict[str, float]):
        exposures = []
        for k, pos in self.positions.items():
            px = prices.get(k, pos.entry)
            exposures.append(
                {
                    "symbol": pos.symbol,
                    "direction": pos.side,
                    "notional_eur": float(px * pos.qty),
                    "risk_pct": float(CFG["risk_per_trade_pct"] * 100.0),
                }
            )
        log_equity(self.equity)
        log_risk(
            open_risk_pct=min(len(self.positions) * CFG["risk_per_trade_pct"] * 100.0, 100.0),
            day_pnl_pct=self.day_pnl_pct(),
            rolling_dd_pct=self.max_dd,
            mode="normal",
        )
        log_position_snapshot(exposures, avg_leverage=1.0)

    def worst_offenders(self, prices: Dict[str, float], topn: int = 2) -> List[Tuple[str, float]]:
        # sortiere nach größtem negativen unrealized PnL
        losses = []
        for k, pos in self.positions.items():
            px = prices.get(k)
            if px is None:
                continue
            pnl = pos.mtm(px)
            losses.append((k, pnl))
        losses.sort(key=lambda t: t[1])  # am negativsten zuerst
        return losses[:topn]


# =============================
# Utils
# =============================
def align_to_next_minute():
    now = datetime.utcnow()
    sleep = 60 - (now.second + now.microsecond / 1e6)
    if sleep > 0:
        time.sleep(sleep)


def write_alert(kind: str, status: dict, offenders: List[Tuple[str, float]], tuning: dict):
    alert = {
        "ts": _now_ms(),
        "when": datetime.utcnow().isoformat() + "Z",
        "kind": kind,
        "status": status,
        "offenders": [{"key": k, "unrealized_pnl": pnl} for k, pnl in offenders],
        "tuning": tuning,
    }
    fn = REPORTS_DIR / f"alert_{alert['ts']}.json"
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(alert, f, ensure_ascii=False, indent=2)
    print("[ALERT]", kind, "->", fn)


# =============================
# Hauptschleife (mit integriertem Guard)
# =============================
def main():
    portfolio = Portfolio(PAPER_CAPITAL)
    align_to_next_minute()
    print("[bot] Live-Signals (SMA/ATR) gestartet – Endlosschleife. Stop mit CTRL+C.")

    # Entry-Pause (Guard)
    pause_new_entries = False

    while True:
        loop_start = time.time()
        prices: Dict[str, float] = {}

        try:
            # --- Bitget ---
            for sym in CFG["symbols"]["bitget"]:
                df = get_bitget_candles(sym, granularity_sec=60, limit=CFG["hist_bars"])
                last_px = float(df["close"].iloc[-1]) if (df is not None and len(df)) else None
                if last_px is not None:
                    prices[f"bitget:{sym}"] = last_px
                if last_px is not None:
                    portfolio.maybe_exit("bitget", sym, last_px)
                if not pause_new_entries and df is not None:
                    sig = signal_from_bars(df, CFG["sma_fast"], CFG["sma_slow"])
                    a = last_atr(df, CFG["atr_len"])
                    if (
                        sig
                        and a
                        and last_px is not None
                        and len(portfolio.positions) < CFG["max_concurrent_positions"]
                    ):
                        rationale = f"SMA{CFG['sma_fast']}/{CFG['sma_slow']} Cross @1m; ATR={a:.4f}"
                        portfolio.open_position("bitget", sym, sig, last_px, a, rationale)

            # --- Alpaca ---
            for sym in CFG["symbols"]["alpaca"]:
                df = get_alpaca_bars(sym, limit=CFG["hist_bars"])
                last_px = float(df["close"].iloc[-1]) if (df is not None and len(df)) else None
                if last_px is not None:
                    prices[f"alpaca:{sym}"] = last_px
                if last_px is not None:
                    portfolio.maybe_exit("alpaca", sym, last_px)
                if not pause_new_entries and df is not None:
                    sig = signal_from_bars(df, CFG["sma_fast"], CFG["sma_slow"])
                    a = last_atr(df, CFG["atr_len"])
                    if (
                        sig
                        and a
                        and last_px is not None
                        and len(portfolio.positions) < CFG["max_concurrent_positions"]
                    ):
                        rationale = f"SMA{CFG['sma_fast']}/{CFG['sma_slow']} Cross @1m; ATR={a:.4f}"
                        portfolio.open_position("alpaca", sym, sig, last_px, a, rationale)

            # Mark-to-market & Logs
            portfolio.mark_to_market(prices)
            portfolio.snapshot_logs(prices)

            # ===== Integrierter Risikowächter =====
            day_pnl = portfolio.day_pnl_pct()
            max_dd = portfolio.max_dd
            breached = (max_dd > MAX_DD_LIMIT) or (day_pnl < DAY_LOSS_LIMIT)

            if breached:
                # 1) Pausiere neue Entries
                pause_new_entries = True
                # 2) Schließe „schlimmste“ offenen Positionen (Top 2 Verluste)
                offenders = portfolio.worst_offenders(prices, topn=2)
                for k, _ in offenders:
                    venue, sym = k.split(":")
                    px = prices.get(k)
                    if px is not None:
                        portfolio.maybe_exit(venue, sym, px)
                # 3) Konservative Tuning-Vorschläge SOFORT übernehmen
                tuning = {
                    "risk_per_trade_pct": 0.005,  # 0.5%
                    "atr_stop_mult": 1.6,
                    "atr_tp_mult": 2.2,
                    "max_concurrent_positions": max(1, min(CFG["max_concurrent_positions"], 2)),
                }
                CFG.update(tuning)
                # 4) Alert schreiben
                write_alert(
                    "RISK_BREACH",
                    {"day_pnl_pct": day_pnl, "max_drawdown_pct": max_dd},
                    offenders,
                    tuning,
                )
            else:
                # Wenn entspannt: Pause wieder aufheben
                pause_new_entries = False

        except KeyboardInterrupt:
            print("\n[bot] Manuell gestoppt. Bye!")
            break
        except Exception as e:
            print("[bot] Fehler:", e)
            traceback.print_exc()

        # auf 1-Minutentakt synchronisieren
        elapsed = time.time() - loop_start
        time.sleep(max(1.0, 60.0 - elapsed))


if __name__ == "__main__":
    main()
