import argparse, csv, json, os, subprocess, time
from pathlib import Path
from typing import List, Dict, Any, Tuple

BRIDGE_OUT = Path(os.getenv("BRIDGE_OUT", "bridge_out"))
EVENTS_DIR = BRIDGE_OUT / "events"
REPORTS_DIR = BRIDGE_OUT / "reports"
SNAP_DIR = BRIDGE_OUT / "snapshots"

for p in [EVENTS_DIR / "trades", EVENTS_DIR / "risk", EVENTS_DIR / "equity", REPORTS_DIR, SNAP_DIR]:
    p.mkdir(parents=True, exist_ok=True)

PAPER_CAPITAL = float(os.getenv("PAPER_CAPITAL", "10000"))

# --- Utils ---


def _read_json_files(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for fn in sorted(path.glob("*.json")):
        try:
            with open(fn, "r", encoding="utf-8") as f:
                items.append(json.load(f))
        except Exception:
            pass
    return items


# --- Metrics ---


def compute_trade_metrics(trades: List[Dict[str, Any]]) -> Tuple[float, float, float, float]:
    """Return (realized_pnl, profit_factor, winrate, max_drawdown_pct)"""
    # Build equity from close events only
    closes = [t for t in trades if t.get("event") == "close"]
    if not closes:
        return 0.0, 0.0, 0.0, 0.0

    pnl_list = [float(t.get("profit", 0.0)) - float(t.get("fees", 0.0)) for t in closes]
    wins = [p for p in pnl_list if p > 0]
    losses = [abs(p) for p in pnl_list if p < 0]

    realized_pnl = sum(pnl_list)
    profit_factor = (sum(wins) / sum(losses)) if losses else (sum(wins) / 1e-9)
    winrate = (len(wins) / len(pnl_list)) * 100.0

    # equity curve
    equity = PAPER_CAPITAL
    peak = PAPER_CAPITAL
    max_dd = 0.0
    for p in pnl_list:
        equity += p
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    max_drawdown_pct = max_dd * 100.0

    return realized_pnl, profit_factor, winrate, max_drawdown_pct


def latest_snapshot() -> Dict[str, Any]:
    snaps = list(sorted(SNAP_DIR.glob("positions_*.json")))
    if not snaps:
        return {"exposures": [], "avg_leverage": 0.0}
    with open(snaps[-1], "r", encoding="utf-8") as f:
        return json.load(f)


def collect_equity_points() -> List[Tuple[int, float]]:
    points: List[Tuple[int, float]] = []
    for fn in sorted((EVENTS_DIR / "equity").glob("*.json")):
        try:
            with open(fn, "r", encoding="utf-8") as f:
                d = json.load(f)
                points.append((int(d.get("ts", 0)), float(d.get("equity", PAPER_CAPITAL))))
        except Exception:
            pass
    return points


def write_equity_csv(points: List[Tuple[int, float]]):
    csv_path = REPORTS_DIR / "equity_curve.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_ms", "equity"])
        for ts, eq in points:
            w.writerow([ts, eq])


def publish_status():
    trades = _read_json_files(EVENTS_DIR / "trades")
    realized_pnl, profit_factor, winrate, max_dd = compute_trade_metrics(trades)

    pts = collect_equity_points()
    if not pts:
        pts = [(int(time.time() * 1000), PAPER_CAPITAL)]
    write_equity_csv(pts)

    snap = latest_snapshot()

    status = {
        "paper_capital": PAPER_CAPITAL,
        "realized_pnl": realized_pnl,
        "profit_factor": round(profit_factor, 3),
        "winrate_pct": round(winrate, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "exposures": snap.get("exposures", []),
        "avg_leverage": snap.get("avg_leverage", 0.0),
        "last_update_ts": int(time.time() * 1000),
        "counts": {"trades_total": len(trades), "equity_points": len(pts)},
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    status_path = REPORTS_DIR / "status.json"
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    if os.getenv("COOP_PUBLISH_TO_GIT", "false").lower() == "true":
        git_publish()

    print("[bridge] published:", status_path)


# --- Optional: Git Publishing ---


def git(*args: str):
    subprocess.check_call(["git", *args], cwd=str(Path.cwd()))


def git_publish():
    try:
        # ensure repo
        if not (Path.cwd() / ".git").exists():
            git("init")
            remote = os.getenv("GIT_REMOTE")
            if remote:
                try:
                    git("remote", "add", "origin", remote)
                except Exception:
                    pass
        # stage & commit
        git("add", str(REPORTS_DIR))
        author_name = os.getenv("GH_AUTHOR_NAME", "bridge-bot")
        author_email = os.getenv("GH_AUTHOR_EMAIL", "bridge@example.com")
        msg = f"bridge: publish {int(time.time())}"
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": author_name,
                "GIT_AUTHOR_EMAIL": author_email,
                "GIT_COMMITTER_NAME": author_name,
                "GIT_COMMITTER_EMAIL": author_email,
            }
        )
        subprocess.check_call(
            ["git", "commit", "-m", msg, "--allow-empty"], cwd=str(Path.cwd()), env=env
        )
        # push
        branch = os.getenv("GIT_BRANCH", "main")
        git("push", "-u", "origin", branch)
        print("[bridge] pushed reports to origin/", branch)
    except Exception as e:
        print("[bridge] git publish failed:", e)


# --- CLI ---


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--publish-interval", type=int, default=60, help="Sekunden zwischen Aggregationen"
    )
    args = ap.parse_args()

    print("[bridge] running; interval =", args.publish_interval, "s; out =", REPORTS_DIR)
    while True:
        try:
            publish_status()
        except Exception as e:
            print("[bridge] error:", e)
        time.sleep(args.publish_interval)


if __name__ == "__main__":
    main()
