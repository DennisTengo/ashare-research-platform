"""IC 分析的单元测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.ic import compute_ic, ic_summary


def _panel(factor_values, return_values, date="2024-01-02"):
    return pd.DataFrame(
        {
            "trade_date": pd.Timestamp(date),
            "factor_value": factor_values,
            "fwd_return": return_values,
        }
    )


def test_perfect_positive_relation_gives_ic_one():
    panel = _panel(np.arange(50, dtype=float), np.arange(50, dtype=float))
    ic = compute_ic(panel)
    assert ic.iloc[0] == pytest.approx(1.0)


def test_perfect_negative_relation_gives_ic_minus_one():
    panel = _panel(np.arange(50, dtype=float), -np.arange(50, dtype=float))
    ic = compute_ic(panel)
    assert ic.iloc[0] == pytest.approx(-1.0)


def test_no_relation_gives_ic_near_zero():
    rng = np.random.default_rng(0)
    panel = _panel(rng.normal(size=300), rng.normal(size=300))
    ic = compute_ic(panel)
    assert abs(ic.iloc[0]) < 0.15


def test_rank_based_ic_ignores_extreme_outlier():
    """一个极端值不应该主导整个相关系数——这是用秩相关的原因。"""
    factors = np.arange(50, dtype=float)
    returns = np.arange(50, dtype=float)
    # 把最后一个收益改成极端大值，秩相关结果应基本不变
    returns[-1] = 1_000_000.0
    panel = _panel(factors, returns)
    ic = compute_ic(panel)
    assert ic.iloc[0] == pytest.approx(1.0)


def test_min_stocks_filter_skips_small_cross_sections():
    panel = _panel(np.arange(10, dtype=float), np.arange(10, dtype=float))
    assert compute_ic(panel, min_stocks=30).empty


def test_summary_statistics():
    ic = pd.Series([0.1, 0.2, -0.1, 0.0, 0.3])
    stats = ic_summary(ic)
    assert stats["periods"] == 5
    assert stats["ic_mean"] == pytest.approx(0.1)
    assert stats["win_rate"] == pytest.approx(0.6)
    assert stats["icir"] == pytest.approx(0.1 / ic.std())


def test_summary_handles_empty_series():
    stats = ic_summary(pd.Series(dtype=float))
    assert stats["periods"] == 0
    assert np.isnan(stats["ic_mean"])


def test_icir_annualized_when_periods_given():
    ic = pd.Series([0.1, 0.2, -0.1, 0.0, 0.3])
    stats = ic_summary(ic, periods_per_year=12)
    assert stats["icir_annual"] == pytest.approx(stats["icir"] * np.sqrt(12))
