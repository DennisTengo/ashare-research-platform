"""组合回测的单元测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.backtest import (
    long_short_returns,
    max_drawdown,
    nav_series,
    performance,
    portfolio_returns,
)


def _panel(rows):
    """rows: [(date, group, code, fwd_return), ...]"""
    return pd.DataFrame(
        [
            {
                "trade_date": pd.Timestamp(d),
                "group": g,
                "code": c,
                "fwd_return": r,
            }
            for d, g, c, r in rows
        ]
    )


def test_first_period_turnover_is_full():
    panel = _panel([("2024-01-02", 5, "A", 0.1), ("2024-01-02", 5, "B", 0.1)])
    out = portfolio_returns(panel, 5, cost_buy=0.001, cost_sell=0.002)
    assert out["turnover"].iloc[0] == 1.0
    # 成本 = 1.0 * (0.001 + 0.002) = 0.003
    assert out["cost"].iloc[0] == pytest.approx(0.003)


def test_unchanged_holdings_have_zero_turnover():
    panel = _panel(
        [
            ("2024-01-02", 5, "A", 0.1),
            ("2024-01-02", 5, "B", 0.1),
            ("2024-01-23", 5, "A", 0.1),
            ("2024-01-23", 5, "B", 0.1),
        ]
    )
    out = portfolio_returns(panel, 5, cost_buy=0.001, cost_sell=0.002)
    assert out["turnover"].iloc[1] == 0.0
    assert out["cost"].iloc[1] == 0.0


def test_partial_replacement_turnover():
    """第二期换掉一半，换手应为 0.5。"""
    panel = _panel(
        [
            ("2024-01-02", 5, "A", 0.1),
            ("2024-01-02", 5, "B", 0.1),
            ("2024-01-23", 5, "A", 0.1),
            ("2024-01-23", 5, "C", 0.1),
        ]
    )
    out = portfolio_returns(panel, 5, cost_buy=0.001, cost_sell=0.002)
    assert out["turnover"].iloc[1] == pytest.approx(0.5)


def test_net_return_subtracts_cost():
    panel = _panel([("2024-01-02", 5, "A", 0.10)])
    out = portfolio_returns(panel, 5, cost_buy=0.001, cost_sell=0.002)
    assert out["net"].iloc[0] == pytest.approx(0.10 - 0.003)


def test_zero_cost_keeps_gross():
    panel = _panel([("2024-01-02", 5, "A", 0.10)])
    out = portfolio_returns(panel, 5, cost_buy=0.0, cost_sell=0.0)
    assert out["net"].iloc[0] == pytest.approx(0.10)


def test_max_drawdown():
    nav = pd.Series([1.0, 1.2, 0.9, 1.1])
    assert max_drawdown(nav) == pytest.approx(0.9 / 1.2 - 1)


def test_performance_compounds():
    returns = pd.Series([0.1, 0.1])
    perf = performance(returns, periods_per_year=2)
    assert perf["total_return"] == pytest.approx(0.21)
    assert perf["annual_return"] == pytest.approx(0.21)


def test_performance_handles_empty():
    perf = performance(pd.Series(dtype=float))
    assert perf["periods"] == 0


def test_nav_series_starts_at_one():
    returns = pd.Series([0.1, -0.05], index=pd.bdate_range("2024-01-02", periods=2))
    nav = nav_series(returns)
    assert nav.iloc[0] == pytest.approx(1.0)
    assert nav.iloc[-1] == pytest.approx(1.1 * 0.95)


def test_long_short_combines_both_legs():
    panel = _panel(
        [
            ("2024-01-02", 5, "A", 0.10),
            ("2024-01-02", 1, "B", 0.02),
        ]
    )
    ls = long_short_returns(panel, n_groups=5, cost_buy=0.0, cost_sell=0.0)
    assert ls["gross"].iloc[0] == pytest.approx(0.08)
