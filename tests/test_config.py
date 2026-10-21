"""配置加载的测试。"""

from __future__ import annotations

from pathlib import Path

from common.config import Config, load_config


def test_fallback_to_example_config():
    """仓库中只有 config.example.yaml 时也能正常加载。"""
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert cfg.source.batch_size > 0


def test_missing_file_uses_defaults():
    """显式路径不存在时退回代码默认值，不抛异常。"""
    cfg = load_config("not_exists_anywhere.yaml")
    assert cfg.backtest.commission_rate == 0.0003
    assert cfg.backtest.trade_price == "next_open"


def test_resolve_relative_path():
    """相对路径按项目根目录展开为绝对路径。"""
    cfg = Config()
    resolved = cfg.resolve(Path("data/ashare.duckdb"))
    assert resolved.is_absolute()
    assert resolved.name == "ashare.duckdb"


def test_invalid_value_rejected():
    """非法参数应被校验拦住。"""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Config(source={"batch_size": 0})
