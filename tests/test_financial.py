"""财务数据采集与 PIT 对齐的单元测试。

对齐部分是重点：它决定财务因子会不会偷用未来信息。
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from collector.financial import _to_float, quarter_end, save
from warehouse.schema import init_schema


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def _record(code, report_period, announce_date, net_profit=100.0):
    return {
        "code": code,
        "report_period": report_period,
        "announce_date": announce_date,
        "revenue": 1000.0,
        "net_profit": net_profit,
        "total_assets": None,
        "total_equity": None,
        "roe": 0.1,
        "eps": 1.0,
        "gross_margin": 0.5,
        "net_margin": 0.2,
        "extra": "{}",
    }


def test_quarter_end_mapping():
    assert quarter_end(2024, 1) == "2024-03-31"
    assert quarter_end(2024, 2) == "2024-06-30"
    assert quarter_end(2024, 3) == "2024-09-30"
    assert quarter_end(2024, 4) == "2024-12-31"


def test_to_float_handles_empty_string():
    assert _to_float("") is None
    assert _to_float(None) is None
    assert _to_float("1.5") == 1.5
    assert _to_float("abc") is None


def test_save_and_read_back(conn):
    n = save(conn, [_record("600519.SH", "2024-03-31", "2024-04-27")])
    assert n == 1
    row = conn.execute(
        "SELECT code, report_period, announce_date, net_profit FROM fact_financial"
    ).fetchone()
    assert row[0] == "600519.SH"
    assert str(row[1]) == "2024-03-31"
    assert str(row[2]) == "2024-04-27"
    assert row[3] == 100.0


def test_save_is_idempotent(conn):
    rec = _record("600519.SH", "2024-03-31", "2024-04-27")
    save(conn, [rec])
    save(conn, [rec])
    assert conn.execute("SELECT count(*) FROM fact_financial").fetchone()[0] == 1


def test_same_period_different_announcement_kept_apart(conn):
    """同一报告期多次公告（快报、更正）应各存一条。"""
    save(
        conn,
        [
            _record("600519.SH", "2024-03-31", "2024-04-10", net_profit=90.0),
            _record("600519.SH", "2024-03-31", "2024-04-27", net_profit=100.0),
        ],
    )
    assert conn.execute("SELECT count(*) FROM fact_financial").fetchone()[0] == 2


def test_asof_join_respects_announce_date(conn):
    """核心用例：交易日只能看到当时已公告的财报。

    2024Q1 报告期 03-31，公告日 04-27。
    在 04-15 这天，能看到的必须是 2023 年报，不是 2024Q1。
    """
    save(
        conn,
        [
            _record("600519.SH", "2023-12-31", "2024-03-28", net_profit=300.0),
            _record("600519.SH", "2024-03-31", "2024-04-27", net_profit=100.0),
        ],
    )
    conn.execute(
        "INSERT INTO fact_daily_price VALUES ('600519.SH', '2024-04-15', 1, 1, 1, 1, 1, 1)"
    )
    conn.execute(
        "INSERT INTO fact_daily_price VALUES ('600519.SH', '2024-04-30', 1, 1, 1, 1, 1, 1)"
    )

    rows = conn.execute(
        """
        SELECT p.trade_date, f.report_period, f.net_profit
        FROM fact_daily_price p
        ASOF LEFT JOIN fact_financial f
          ON p.code = f.code AND p.trade_date >= f.announce_date
        ORDER BY p.trade_date
        """
    ).fetchall()

    assert str(rows[0][1]) == "2023-12-31", "4-15 不应看到 4-27 才公告的一季报"
    assert rows[0][2] == 300.0
    assert str(rows[1][1]) == "2024-03-31", "4-30 应看到已公告的一季报"
    assert rows[1][2] == 100.0
