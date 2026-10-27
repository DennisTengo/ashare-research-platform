"""K 线周期聚合。

这些测试守的是聚合口径：开盘取首、收盘取末、高低取极值、量额累加。
口径错了不会报错，只会画出一根看起来正常但数字不对的蜡烛，
只能靠测试拦。
"""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.resample import to_period


def _bars() -> pd.DataFrame:
    """两周共六个交易日，数值刻意各不相同，便于定位取错的可能。"""
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(
                [
                    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
                    "2024-01-08", "2024-01-09",
                ]
            ),
            "open": [10.0, 11.0, 12.0, 13.0, 20.0, 21.0],
            "high": [10.5, 11.5, 12.5, 13.5, 20.5, 21.5],
            "low": [9.5, 10.5, 11.5, 12.5, 19.5, 20.5],
            "close": [10.8, 11.8, 12.8, 13.8, 20.8, 21.8],
            "volume": [100, 200, 300, 400, 500, 600],
            "amount": [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0],
        }
    )


def test_daily_returns_input_unchanged():
    bars = _bars()
    assert to_period(bars, "D") is bars


def test_weekly_uses_first_open_and_last_close():
    weekly = to_period(_bars(), "W")
    assert len(weekly) == 2

    first = weekly.iloc[0]
    assert first["open"] == 10.0      # 1/2 开盘
    assert first["close"] == 13.8     # 1/5 收盘
    assert first["high"] == 13.5
    assert first["low"] == 9.5
    assert first["volume"] == 1000    # 100+200+300+400
    assert first["amount"] == 10000.0


def test_weekly_x_axis_is_last_trading_day():
    weekly = to_period(_bars(), "W")
    assert weekly.iloc[0]["trade_date"] == pd.Timestamp("2024-01-05")
    assert weekly.iloc[1]["trade_date"] == pd.Timestamp("2024-01-09")


def test_monthly_and_yearly_collapse_to_one_bar():
    assert len(to_period(_bars(), "M")) == 1
    assert len(to_period(_bars(), "Y")) == 1

    month = to_period(_bars(), "M").iloc[0]
    assert month["open"] == 10.0
    assert month["close"] == 21.8
    assert month["volume"] == 2100


def test_empty_input_returns_empty():
    empty = pd.DataFrame(
        columns=["trade_date", "open", "high", "low", "close", "volume", "amount"]
    )
    assert to_period(empty, "W").empty


def test_unknown_frequency_raises():
    with pytest.raises(ValueError, match="不支持的周期"):
        to_period(_bars(), "Q")
