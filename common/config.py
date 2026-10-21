"""配置加载。

所有路径与参数集中在这里，业务代码中不出现硬编码路径。
真实配置写在 config/config.yaml（已被 gitignore），仓库里只留示例。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "config.example.yaml"


class DataConfig(BaseModel):
    """数据落盘位置。"""

    root: Path = Path("data")
    database: Path = Path("data/ashare.duckdb")
    staging: Path = Path("staging")


class SourceConfig(BaseModel):
    """数据源。primary 为主源，失败时由调用方降级到 fallback。"""

    primary: str = "baostock"
    fallback: str = "akshare"
    batch_size: int = Field(default=100, gt=0)
    sleep_seconds: float = Field(default=2.0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    timeout_seconds: int = Field(default=30, gt=0)


class UniverseConfig(BaseModel):
    """默认股票池定义。"""

    default: str = "all_a"
    exclude_st: bool = True
    exclude_new_days: int = 60


class BacktestConfig(BaseModel):
    """回测成交与成本假设。"""

    commission_rate: float = Field(default=0.0003, ge=0)
    stamp_tax: float = Field(default=0.001, ge=0)
    slippage: float = Field(default=0.001, ge=0)
    benchmark: str = "000300.SH"
    trade_price: Literal["next_open", "next_close"] = "next_open"


class Config(BaseModel):
    data: DataConfig = Field(default_factory=DataConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)

    def resolve(self, p: Path) -> Path:
        """把配置里的相对路径按项目根目录展开。"""
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    @property
    def database_path(self) -> Path:
        return self.resolve(self.data.database)

    @property
    def staging_path(self) -> Path:
        return self.resolve(self.data.staging)


def load_config(path: str | Path | None = None) -> Config:
    """加载配置。

    优先级：显式传入的路径 > config/config.yaml > config/config.example.yaml > 代码默认值。
    """
    candidate = Path(path) if path is not None else DEFAULT_CONFIG
    if not candidate.exists():
        candidate = EXAMPLE_CONFIG
    if not candidate.exists():
        return Config()

    with candidate.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Config(**raw)
