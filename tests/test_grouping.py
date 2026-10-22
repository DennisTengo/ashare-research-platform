"""分组分析的单元测试。

只测纯计算，不碰数据库。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.grouping import (
    add_forward_return,
    add_listed_days,
    assign_double_groups,
    assign_groups,
    daily_group_returns,
    is_monotonic,
    long_short,
    summarize,
)


def _bars(prices, code="000001.SZ"):
    """构造单只股票的日线，日期为连续工作日。"""
    dates = pd.bdate_range("2024-01-01", periods=len(prices))
    return pd.DataFrame(
        {"code": code, "trade_date": dates, "adj_close": prices}
    )


def test_forward_return_skips_next_day_open():
    """T 日因子，T+1 买入，T+1+1 卖出，持有 1 日。"""
    bars = _bars([10.0, 11.0, 12.0, 13.0])
    out = add_forward_return(bars, horizon=1)
    # T=0: close[2]/close[1]-1 = 12/11-1
    assert out["fwd_return"].iloc[0] == pytest.approx(12 / 11 - 1)
    # T=1: close[3]/close[2]-1 = 13/12-1
    assert out["fwd_return"].iloc[1] == pytest.approx(13 / 12 - 1)
    # 末尾不足，应为 NaN
    assert pd.isna(out["fwd_return"].iloc[-1])


def test_forward_return_does_not_use_same_day():
    """T 日收益必须从 T+1 开始，否则会偷用当日涨幅。"""
    bars = _bars([10.0, 100.0, 100.0])
    out = add_forward_return(bars, horizon=1)
    # T=0 的收益应是 100/100-1=0，而不是 100/10-1=9
    assert out["fwd_return"].iloc[0] == pytest.approx(0.0)


def test_listed_days_counts_from_first_row():
    bars = pd.concat([_bars([10, 11, 12], "000001.SZ"), _bars([20, 21], "000002.SZ")])
    out = add_listed_days(bars)
    assert out[out["code"] == "000001.SZ"]["listed_days"].tolist() == [1, 2, 3]
    assert out[out["code"] == "000002.SZ"]["listed_days"].tolist() == [1, 2]


def test_assign_groups_splits_evenly():
    """100 只股票分 5 组，每组 20 只。"""
    panel = pd.DataFrame(
        {
            "trade_date": pd.Timestamp("2024-01-02"),
            "factor_value": np.arange(100, dtype=float),
        }
    )
    out = assign_groups(panel, n_groups=5)
    assert out["group"].value_counts().sort_index().tolist() == [20, 20, 20, 20, 20]
    # 因子值最小的应落在第 1 组
    assert out.loc[out["factor_value"] == 0, "group"].iloc[0] == 1
    assert out.loc[out["factor_value"] == 99, "group"].iloc[0] == 5


def test_assign_groups_drops_days_with_too_few_stocks():
    panel = pd.DataFrame(
        {
            "trade_date": [pd.Timestamp("2024-01-02")] * 10,
            "factor_value": np.arange(10, dtype=float),
        }
    )
    out = assign_groups(panel, n_groups=5)  # 需要至少 25 只
    assert out.empty


def test_assign_groups_ignores_missing_factor_values():
    panel = pd.DataFrame(
        {
            "trade_date": [pd.Timestamp("2024-01-02")] * 30,
            "factor_value": [np.nan] * 5 + list(range(25)),
        }
    )
    out = assign_groups(panel, n_groups=5)
    assert len(out) == 25
    assert out["group"].notna().all()


def test_assign_double_groups_layers_and_groups():
    """300 只股票，3 层 × 5 组，每组 20 只。"""
    n = 300
    panel = pd.DataFrame(
        {
            "trade_date": [pd.Timestamp("2024-01-02")] * n,
            "factor_value": np.arange(n, dtype=float),
            "layer_value": np.arange(n, dtype=float),
        }
    )
    out = assign_double_groups(panel, n_groups=5, n_layers=3)
    assert len(out) == n
    assert out["layer"].value_counts().sort_index().tolist() == [100, 100, 100]
    counts = out.groupby(["layer", "group"]).size()
    assert (counts == 20).all()
    # 层内分组应基于因子值：层内因子最大的落在第 5 组
    bottom = out[out["layer"] == 1]
    assert bottom.loc[bottom["factor_value"].idxmax(), "group"] == 5


def test_assign_double_groups_skips_small_days():
    panel = pd.DataFrame(
        {
            "trade_date": [pd.Timestamp("2024-01-02")] * 50,
            "factor_value": np.arange(50, dtype=float),
            "layer_value": np.arange(50, dtype=float),
        }
    )
    # 需要至少 3*5*5=75 只
    assert assign_double_groups(panel, n_groups=5, n_layers=3).empty


def test_daily_group_returns_averages_within_group():
    panel = pd.DataFrame(
        {
            "trade_date": [pd.Timestamp("2024-01-02")] * 2,
            "group": [1, 1],
            "fwd_return": [0.10, 0.20],
        }
    )
    out = daily_group_returns(panel)
    assert out.loc[pd.Timestamp("2024-01-02"), 1] == pytest.approx(0.15)


def test_summarize_and_long_short():
    daily = pd.DataFrame(
        {1: [0.001] * 10, 5: [0.002] * 10},
        index=pd.bdate_range("2024-01-01", periods=10),
    )
    stats = summarize(daily, n_groups=5)
    assert len(stats) == 2
    assert stats["annual_return"].iloc[1] > stats["annual_return"].iloc[0]

    spread = long_short(daily, n_groups=5)
    assert spread["period_mean"] == pytest.approx(0.001)


def test_is_monotonic_detects_direction():
    increasing = pd.DataFrame(
        {1: [0.001] * 5, 2: [0.002] * 5, 3: [0.003] * 5},
        index=pd.bdate_range("2024-01-01", periods=5),
    )
    assert is_monotonic(increasing, n_groups=3)

    mixed = pd.DataFrame(
        {1: [0.001] * 5, 2: [0.003] * 5, 3: [0.002] * 5},
        index=pd.bdate_range("2024-01-01", periods=5),
    )
    assert not is_monotonic(mixed, n_groups=3)
