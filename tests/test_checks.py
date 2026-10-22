"""数据质量校验的单元测试。

每个用例都手工构造少量数据来触发一条规则，不依赖真实数据库和网络。
"""

from __future__ import annotations

import duckdb
import pytest

from quality.checks import (
    check_date_gap,
    check_duplicate,
    check_factor_monotonic,
    check_price_invalid,
    check_return_limit,
    check_zero_volume_price_move,
    limit_for,
    run_all,
)
from warehouse.schema import init_schema


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def _price(conn, code, dt, o, h, low, c, volume=1000):
    conn.execute(
        "INSERT INTO fact_daily_price (code, trade_date, open, high, low, close, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [code, dt, o, h, low, c, volume, c * volume],
    )


def _factor(conn, code, dt, value):
    conn.execute(
        "INSERT INTO fact_adj_factor (code, trade_date, adj_factor) VALUES (?, ?, ?)",
        [code, dt, value],
    )


def _stock(conn, code, board="主板", is_st=False):
    conn.execute(
        "INSERT INTO dim_stock (code, name, board, is_st) VALUES (?, ?, ?, ?)",
        [code, "测试股", board, is_st],
    )


def _calendar(conn, days):
    for d in days:
        conn.execute(
            "INSERT INTO dim_calendar (trade_date, is_open) VALUES (?, TRUE)", [d]
        )


def test_limit_for_board_and_st():
    assert limit_for("主板", False) == pytest.approx(0.115)
    assert limit_for("创业板", False) == pytest.approx(0.215)
    assert limit_for("科创板", False) == pytest.approx(0.215)
    assert limit_for("主板", True) == pytest.approx(0.065)
    assert limit_for(None, False) == pytest.approx(0.115)


def test_price_invalid_catches_non_positive_and_bad_range(conn):
    _price(conn, "000001.SZ", "2024-01-02", 10, 11, 9, 0)  # 收盘为 0
    _price(conn, "000001.SZ", "2024-01-03", 10, 11, 9, 10.5)
    _price(conn, "000002.SZ", "2024-01-02", 10, 9, 11, 10)  # high < low
    findings = check_price_invalid(conn)
    assert len(findings) == 2
    assert all(f.severity == "error" for f in findings)


def test_price_invalid_passes_normal_rows(conn):
    _price(conn, "000001.SZ", "2024-01-02", 10, 11, 9, 10.5)
    assert check_price_invalid(conn) == []


def test_duplicate_detects_nothing_when_primary_key_guards(conn):
    """主键天然阻止重复，这条检查是防御性的，此处验证正常数据不误报。"""
    _price(conn, "000001.SZ", "2024-01-02", 10, 11, 9, 10.5)
    assert check_duplicate(conn) == []


def test_factor_monotonic_flags_decrease(conn):
    _factor(conn, "000001.SZ", "2024-01-02", 1.0)
    _factor(conn, "000001.SZ", "2024-01-03", 1.05)
    _factor(conn, "000001.SZ", "2024-01-04", 1.02)  # 下降
    findings = check_factor_monotonic(conn)
    assert len(findings) == 1
    assert findings[0].trade_date.isoformat() == "2024-01-04"


def test_factor_monotonic_accepts_flat_and_rising(conn):
    _factor(conn, "000001.SZ", "2024-01-02", 1.0)
    _factor(conn, "000001.SZ", "2024-01-03", 1.0)
    _factor(conn, "000001.SZ", "2024-01-04", 1.3)
    assert check_factor_monotonic(conn) == []


def test_return_limit_ignores_split_when_using_adjusted_price(conn):
    """除权日原始价腰斩、但复权价连续，不应被判为超限。

    这是本模块最重要的用例：如果用原始价判断，所有高送转都会被误报。
    """
    _stock(conn, "000001.SZ", board="主板")
    _price(conn, "000001.SZ", "2024-01-02", 20, 20, 20, 20)
    _factor(conn, "000001.SZ", "2024-01-02", 1.0)
    _price(conn, "000001.SZ", "2024-01-03", 10, 10, 10, 10)  # 10 送 10
    _factor(conn, "000001.SZ", "2024-01-03", 2.0)  # 因子同步翻倍，复权价连续
    assert check_return_limit(conn) == []


def test_return_limit_flags_real_jump(conn):
    _stock(conn, "000001.SZ", board="主板")
    days = [f"2024-01-{d:02d}" for d in range(2, 9)]  # 7 个交易日
    for d in days[:6]:
        _price(conn, "000001.SZ", d, 10, 10, 10, 10)
        _factor(conn, "000001.SZ", d, 1.0)
    _price(conn, "000001.SZ", days[6], 13, 13, 13, 13)  # +30%
    _factor(conn, "000001.SZ", days[6], 1.0)
    findings = check_return_limit(conn)
    assert len(findings) == 1
    assert findings[0].severity == "warning"


def test_return_limit_skips_first_five_days(conn):
    """注册制新股上市前 5 日不设涨跌幅限制，不应判为超限。"""
    _stock(conn, "000001.SZ", board="主板")
    _price(conn, "000001.SZ", "2024-01-02", 10, 10, 10, 10)
    _factor(conn, "000001.SZ", "2024-01-02", 1.0)
    _price(conn, "000001.SZ", "2024-01-03", 20, 20, 20, 20)
    _factor(conn, "000001.SZ", "2024-01-03", 1.0)
    assert check_return_limit(conn) == []


def test_return_limit_uses_wider_band_for_chinext(conn):
    """创业板 ±20%；涨 18% 不应报。"""
    _stock(conn, "300001.SZ", board="创业板")
    days = [f"2024-01-{d:02d}" for d in range(2, 9)]
    for d in days[:6]:
        _price(conn, "300001.SZ", d, 10, 10, 10, 10)
        _factor(conn, "300001.SZ", d, 1.0)
    _price(conn, "300001.SZ", days[6], 11.8, 11.8, 11.8, 11.8)
    _factor(conn, "300001.SZ", days[6], 1.0)
    assert check_return_limit(conn) == []


def test_date_gap_flags_missing_trading_days(conn):
    days = [f"2024-01-{d:02d}" for d in range(2, 12)]  # 10 个交易日
    _calendar(conn, days)
    # 首末日期覆盖整个区间，中间缺 01-04 ~ 01-10 共 7 个交易日
    for d in ["2024-01-02", "2024-01-03", "2024-01-11"]:
        _price(conn, "000001.SZ", d, 10, 10, 10, 10)
    findings = check_date_gap(conn, min_missing=5)
    assert len(findings) == 1
    assert "缺失 7 个交易日" in findings[0].detail


def test_date_gap_passes_complete_series(conn):
    days = [f"2024-01-{d:02d}" for d in range(2, 12)]
    _calendar(conn, days)
    for d in days:
        _price(conn, "000001.SZ", d, 10, 10, 10, 10)
    assert check_date_gap(conn, min_missing=5) == []


def test_date_gap_reports_empty_calendar(conn):
    """日历为空时必须报错，而不是静默返回空结果。"""
    _price(conn, "000001.SZ", "2024-01-02", 10, 10, 10, 10)
    findings = check_date_gap(conn)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "日历为空" in findings[0].detail


def test_zero_volume_with_price_move(conn):
    _price(conn, "000001.SZ", "2024-01-02", 10, 10, 10, 10)
    _factor(conn, "000001.SZ", "2024-01-02", 1.0)
    _price(conn, "000001.SZ", "2024-01-03", 11, 11, 11, 11, volume=0)
    _factor(conn, "000001.SZ", "2024-01-03", 1.0)
    findings = check_zero_volume_price_move(conn)
    assert len(findings) == 1


def test_zero_volume_without_price_move_is_normal(conn):
    """停牌日：量为 0 且价格不变，属正常。"""
    _price(conn, "000001.SZ", "2024-01-02", 10, 10, 10, 10)
    _factor(conn, "000001.SZ", "2024-01-02", 1.0)
    _price(conn, "000001.SZ", "2024-01-03", 10, 10, 10, 10, volume=0)
    _factor(conn, "000001.SZ", "2024-01-03", 1.0)
    assert check_zero_volume_price_move(conn) == []


def test_run_all_returns_every_rule(conn):
    results = run_all(conn)
    assert set(results) == {
        "price_invalid",
        "duplicate",
        "factor_monotonic",
        "return_limit",
        "date_gap",
        "zero_volume_price_move",
    }
