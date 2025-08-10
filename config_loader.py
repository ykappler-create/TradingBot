from pathlib import Path
from typing import Any, Dict
import yaml
from pydantic import BaseModel, Field


class Risk(BaseModel):
    risk_per_trade_pct: float = Field(0.005, ge=0.0, le=0.05)
    max_drawdown_pct: float = Field(8.0, gt=0.0, le=80.0)
    day_loss_limit_pct: float = Field(-3.0, lt=0.0)
    atr_stop_mult: float = Field(1.6, gt=0.0, le=10.0)
    atr_tp_mult: float = Field(2.2, gt=0.0, le=20.0)
    max_concurrent_positions: int = Field(2, ge=1, le=50)


class AppConfig(BaseModel):
    risk: Risk = Risk()


def load_config(path: Path = Path("config/config.yaml")) -> AppConfig:
    data: Dict[str, Any] = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    return AppConfig.model_validate(data)
