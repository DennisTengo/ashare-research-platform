"""编排层的单元测试。

用内存库构造 60 只股票的合成数据，验证 build_panel 与 run_backtest
的结构正确性和方向正确性，不依赖真实数据。
"""

from __future__ import annotations

import json

import duckdb
import pandas as pd
import pytest

from analysis.pipeline import build_panel, drawdown_series, run_backtest
from warehouse.schema import init_schema

N_STOCKS = 60
N_DAYS = 40


def _seed(conn):
    """构造合成数据：因子值越大的股票涨得越快，用来验证方向。"""
    dates = pd.bdate_range("2024-01-01", periods=N_DAYS)
    stocks, prices, factors, values = [], [], [], []
    for i in range(N_STOCKS):
        code = f"{i + 1:06d}.SZ"
        stocks.append((code, f"股{i}", "主板", False))
        for j, d in enumerate(dates):
            # 增长率与因子值 i 正相关：i 越大涨得越快
            close = 10.0 * (1.0 + 0.001 * i * j)
            prices.append(
                (code, d.date(), close, close, close, close, 1000, close * 1000)
            )
            factors.append((code, d.date(), 1.0))
            values.append(("test_factor", d.date(), code, float(i)))

    conn.executemany(
        "INSERT INTO dim_stock (code, name, board, is_st) VALUES (?, ?, ?, ?)", stocks
    )
    conn.executemany(
        "INSERT INTO fact_daily_price "
        "(code, trade_date, open, high, low, close, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        prices,
    )
    conn.executemany(
        "INSERT INTO fact_adj_factor (code, trade_date, adj_factor) VALUES (?, ?, ?)",
        factors,
    )
    conn.executemany(
        "INSERT INTO fact_factor (factor_name, trade_date, code, value) VALUES (?, ?, ?, ?)",
        values,
    )


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    init_schema(c)
    _seed(c)
    yield c
    c.close()


def test_build_panel_has_expected_columns(conn):
    panel = build_panel(conn, "test_factor", horizon=5, groups=5, min_listed=10)
    assert not panel.empty
    for col in ["code", "trade_date", "factor_value", "fwd_return", "group", "listed_days"]:
        assert col in panel.columns


def test_build_panel_without_groups(conn):
    panel = build_panel(conn, "test_factor", horizon=5, groups=None, min_listed=10)
    assert "group" not in panel.columns


def test_build_panel_with_extra_factor(conn):
    conn.execute(
        "INSERT INTO fact_factor VALUES ('layer_f', '2024-01-02', '000001.SZ', 1.0)"
    )
    panel = build_panel(
        conn,
        "test_factor",
        horizon=5,
        groups=None,
        min_listed=10,
        extra_factors={"layer_value": "test_factor"},
    )
    assert "layer_value" in panel.columns


def test_build_panel_missing_factor_returns_empty(conn):
    assert build_panel(conn, "no_such_factor").empty


def test_run_backtest_returns_structured_result(conn):
    result = run_backtest(conn, "test_factor", horizon=5, groups=5, min_listed=10)
    assert result.n_periods > 0
    # 基准 + 5 组 + 多空(毛/净)
    assert len(result.groups) == 8
    assert "G5" in result.nav
    assert "多空(净)" in result.nav
    assert len(result.drawdown) == len(result.nav)


def test_high_factor_group_outperforms(conn):
    """合成数据里因子值越大涨得越快，G5 应胜过 G1。"""
    result = run_backtest(
        conn, "test_factor", horizon=5, groups=5, min_listed=10,
        cost_buy=0.0, cost_sell=0.0,
    )
    stats = result.groups.set_index("label")
    assert stats.loc["G5", "annual_return"] > stats.loc["G1", "annual_return"]


def test_nav_starts_at_one(conn):
    result = run_backtest(conn, "test_factor", horizon=5, groups=5, min_listed=10)
    for series in result.nav.values():
        assert series.iloc[0] == pytest.approx(1.0)


def test_to_dict_is_json_serializable(conn):
    result = run_backtest(conn, "test_factor", horizon=5, groups=5, min_listed=10)
    payload = json.dumps(result.to_dict())
    assert "test_factor" in payload


def test_missing_factor_yields_zero_periods(conn):
    result = run_backtest(conn, "no_such_factor")
    assert result.n_periods == 0


def test_drawdown_series_is_non_positive(conn):
    nav = pd.Series([1.0, 1.2, 0.9, 1.1])
    dd = drawdown_series(nav)
    assert (dd <= 0).all()
    assert dd.min() == pytest.approx(0.9 / 1.2 - 1)
