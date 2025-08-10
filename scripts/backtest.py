#!/usr/bin/env python
import argparse, sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Backtest-Report: Equity-Curve-Stats + optionaler Plot"
    )
    parser.add_argument(
        "--csv",
        default="bridge_out/reports/equity_curve.csv",
        help="Pfad zur Equity-Curve-CSV (Spalte 'equity' oder letzte Spalte)",
    )
    parser.add_argument(
        "--out", default="bridge_out/reports/equity_curve.png", help="Pfad für den Plot (PNG)"
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV nicht gefunden: {csv_path}")
        sys.exit(1)

    try:
        import pandas as pd
    except Exception as e:
        print("pandas nicht installiert:", e)
        sys.exit(1)

    df = pd.read_csv(csv_path)
    if df.shape[1] == 1:
        df.columns = ["equity"]
    elif "equity" not in df.columns:
        df.rename(columns={df.columns[-1]: "equity"}, inplace=True)

    eq = df["equity"].astype(float)
    start, end = eq.iloc[0], eq.iloc[-1]
    total_return = (end / start) - 1 if start else float("nan")
    rets = eq.pct_change().dropna()
    vol = (rets.std() * (len(rets) ** 0.5)) if not rets.empty else float("nan")

    running_max = eq.cummax()
    drawdown = (eq / running_max) - 1.0
    max_dd = drawdown.min()

    print(f"Start: {start:.2f}")
    print(f"Ende:  {end:.2f}")
    print(f"Total Return: {total_return*100:.2f}%")
    print(f"Max Drawdown: {max_dd*100:.2f}%")
    print(f"Volatilität (ann. grob): {vol*100:.2f}%")

    # Plot (falls matplotlib verfügbar)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.figure()
        plt.plot(eq.values)
        plt.title("Equity Curve")
        plt.xlabel("Index")
        plt.ylabel("Equity")
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(out_path, bbox_inches="tight")
        print(f"Plot gespeichert: {out_path}")
    except Exception as e:
        print("Plot übersprungen:", e)


if __name__ == "__main__":
    main()
