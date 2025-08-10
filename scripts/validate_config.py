#!/usr/bin/env python
from pathlib import Path
import sys

# Projekt-Root in sys.path aufnehmen (damit "config_loader" gefunden wird)
sys.path.append(str(Path(__file__).resolve().parent.parent))

from config_loader import load_config


def main():
    cfg = load_config(Path("config/config.yaml"))
    r = cfg.risk
    print("CONFIG OK")
    print(f" risk_per_trade_pct      = {r.risk_per_trade_pct}")
    print(f" max_drawdown_pct        = {r.max_drawdown_pct}")
    print(f" day_loss_limit_pct      = {r.day_loss_limit_pct}")
    print(f" atr_stop_mult           = {r.atr_stop_mult}")
    print(f" atr_tp_mult             = {r.atr_tp_mult}")
    print(f" max_concurrent_positions= {r.max_concurrent_positions}")


if __name__ == "__main__":
    main()
