"""财务因子的单元测试。

重点不在“因子算得准不准”（ROE 本身就是取数），而在三件容易错的事：
  1. 财报按公告日而不是报告期生效；
  2. 累计 ROE 正确年化，不制造季节性伪信号；
  3. 同比在分母为负时不硬算。
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

import factor.factors  # noqa: F401  触发注册
from analysis.financial import load_panel_with_financial
from factor.base import Factor, all_factors, register
from factor.engine import compute_all
from warehouse.schema import init_schema


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def _price(conn, code, day, close=10.0, factor=1.0):
    conn.execute(
        "INSERT INTO fact_daily_price (code, trade_date, open, high, low, close, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, 1000, ?)",
        [code, day, close, close, close, close, close * 1000],
    )
    conn.execute(
        "INSERT INTO fact_adj_factor (code, trade_date, adj_factor) VALUES (?, ?, ?)",
        [code, day, factor],
    )


def _fin(conn, code, report_period, announce_date, roe=None, net_profit=None):
    conn.execute(
        "INSERT INTO fact_financial "
        "(code, report_period, announce_date, roe, net_profit) VALUES (?, ?, ?, ?, ?)",
        [code, report_period, announce_date, roe, net_profit],
    )


def test_roe_annualized_by_quarter(conn):
    """累计 ROE 要按季度数年化，否则一季报会让所有股票 ROE 骤降。"""
    _financial_only = [
        ("2024-03-31", "2024-04-25", 0.05, 0.20),  # Q1 → ×4 → 0.20
        ("2024-06-30", "2024-08-25", 0.10, 0.20),  # H1 → ×2 → 0.20
        ("2024-09-30", "2024-10-25", 0.15, 0.20),  # 前三季 → ×4/3 → 0.20
        ("2024-12-31", "2025-03-25", 0.20, 0.20),  # 全年 → ×1 → 0.20
    ]
    for period, announce, cumulative, _ in _financial_only:
        _fin(conn, "600519.SH", period, announce, roe=cumulative)
        _price(conn, "600519.SH", announce)

    df = load_panel_with_financial(conn, ("roe_annualized",))
    values = df.sort_values("trade_date")["roe_annualized"].tolist()
    assert values == pytest.approx([0.20, 0.20, 0.20, 0.20])


def test_financial_visible_only_after_announcement(conn):
    """核心用例：公告前看不到新财报。"""
    _fin(conn, "600519.SH", "2023-12-31", "2024-03-28", roe=0.30)
    _fin(conn, "600519.SH", "2024-03-31", "2024-04-27", roe=0.05)
    for day in ("2024-04-15", "2024-04-30"):
        _price(conn, "600519.SH", day)

    df = load_panel_with_financial(conn, ("roe_annualized",)).sort_values("trade_date")
    assert df.iloc[0]["roe_annualized"] == pytest.approx(0.30), "4-15 应仍用年报"
    assert df.iloc[1]["roe_annualized"] == pytest.approx(0.20), "4-30 应换成一季报年化"


def test_net_profit_yoy_skips_negative_base(conn):
    """去年同期亏损时不计算增速，返回空值而不是一个无意义的百分比。"""
    _fin(conn, "000001.SZ", "2023-06-30", "2023-08-25", net_profit=-100.0)
    _fin(conn, "000001.SZ", "2024-06-30", "2024-08-25", net_profit=200.0)
    _price(conn, "000001.SZ", "2024-09-02")

    df = load_panel_with_financial(conn, ("net_profit_yoy",))
    assert pd.isna(df["net_profit_yoy"].iloc[0])


def test_net_profit_yoy_computed_on_positive_base(conn):
    _fin(conn, "000001.SZ", "2023-06-30", "2023-08-25", net_profit=100.0)
    _fin(conn, "000001.SZ", "2024-06-30", "2024-08-25", net_profit=150.0)
    _price(conn, "000001.SZ", "2024-09-02")

    df = load_panel_with_financial(conn, ("net_profit_yoy",))
    assert df["net_profit_yoy"].iloc[0] == pytest.approx(0.5)


def test_roe_factor_clips_extreme(conn):
    """净资产近零导致的离谱 ROE 必须被截尾，否则会破坏分组排序。"""
    _fin(conn, "600221.SH", "2024-06-30", "2024-08-30", roe=-6.01)
    _price(conn, "600221.SH", "2024-09-02")

    compute_all(conn, ["roe"])
    value = conn.execute(
        "SELECT value FROM fact_factor WHERE factor_name = 'roe'"
    ).fetchone()[0]
    assert value == pytest.approx(-1.0)


def test_compute_all_handles_financial_and_price_factors_together(conn):
    """价格因子和财务因子混算时，引擎只合并一次财务数据且互不干扰。"""
    _fin(conn, "600519.SH", "2024-03-31", "2024-04-25", roe=0.05)
    for i in range(25):
        _price(conn, "600519.SH", f"2024-05-{i+1:02d}" if i < 31 else "2024-06-01", 10.0 + i)

    counts = compute_all(conn, ["momentum_20d", "roe"])
    assert counts["momentum_20d"] > 0
    assert counts["roe"] == 25


def test_missing_financial_data_yields_no_factor_value(conn):
    """没有财报的交易日应输出空值，不能拿 0 顶上。"""
    _price(conn, "301999.SZ", "2024-01-02")
    counts = compute_all(conn, ["roe"])
    assert counts["roe"] == 0


def test_register_rejects_unknown_financial_field():
    with pytest.raises(ValueError, match="不可用的财务字段"):
        register(
            Factor(
                name="bad_factor",
                description="测试用",
                lookback=1,
                fn=lambda bars: bars["x"],
                requires=("not_a_field",),
            )
        )


def test_quality_factors_registered():
    names = set(all_factors())
    assert {"roe", "net_profit_yoy"} <= names
