"""批量采集执行器的单元测试。

重点覆盖断点续跑和失误隔离——这两条是之前断线事故直接暴露出来的，
属于「坏了会静默出错」的逻辑，必须有测试兜住。
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from collector import runner
from warehouse.schema import init_schema


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def _last_day(conn, code):
    row = conn.execute(
        "SELECT max(trade_date) FROM fact_daily_price WHERE code = ?", [code]
    ).fetchone()
    return row[0] if row and row[0] else None


def _seed_price(conn, code, day):
    conn.execute(
        "INSERT INTO fact_daily_price VALUES (?, ?, 10, 10, 10, 10, 1000, 10000)",
        [code, day],
    )


def test_skips_symbol_already_up_to_date(conn, monkeypatch):
    _seed_price(conn, "000001.SZ", "2024-01-05")
    calls = []

    def fake_fetch(symbol, start, end, source="baostock"):
        calls.append(symbol)
        return pd.DataFrame(), pd.DataFrame()

    monkeypatch.setattr(runner, "fetch_symbol", fake_fetch)
    result = runner.sync_prices(conn, ["000001.SZ"], "2020-01-01", "2024-01-05", sleep_seconds=0)
    assert result.skipped == 1
    assert result.succeeded == 0
    assert calls == [], "已是最新就不应该发起抓取"


def test_resumes_from_next_day(conn, monkeypatch):
    _seed_price(conn, "000001.SZ", "2024-01-05")
    started = {}

    def fake_fetch(symbol, start, end, source="baostock"):
        started[symbol] = start
        return pd.DataFrame(), pd.DataFrame()

    monkeypatch.setattr(runner, "fetch_symbol", fake_fetch)
    runner.sync_prices(conn, ["000001.SZ"], "2020-01-01", "2024-01-10", sleep_seconds=0)
    assert started["000001.SZ"] == "2024-01-06"


def test_single_failure_does_not_break_batch(conn, monkeypatch):
    def fake_fetch(symbol, start, end, source="baostock"):
        if symbol == "000001.SZ":
            raise RuntimeError("模拟网络中断")
        return pd.DataFrame(), pd.DataFrame()

    monkeypatch.setattr(runner, "fetch_symbol", fake_fetch)
    result = runner.sync_prices(
        conn,
        ["000001.SZ", "000002.SZ"],
        "2024-01-01",
        "2024-01-10",
        max_retries=2,
        sleep_seconds=0,
    )
    assert result.failed == 1
    assert result.succeeded == 1
    assert len(result.failures) == 1
    assert result.failures[0][0] == "000001.SZ"


def test_retries_before_giving_up(conn, monkeypatch):
    attempts = {"n": 0}

    def flaky_fetch(symbol, start, end, source="baostock"):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("临时失败")
        return pd.DataFrame(), pd.DataFrame()

    monkeypatch.setattr(runner, "fetch_symbol", flaky_fetch)
    result = runner.sync_prices(
        conn, ["000001.SZ"], "2024-01-01", "2024-01-10", max_retries=3, sleep_seconds=0
    )
    assert result.succeeded == 1
    assert attempts["n"] == 3


def test_summary_reports_all_buckets(conn, monkeypatch):
    _seed_price(conn, "000003.SZ", "2024-01-10")

    def fake_fetch(symbol, start, end, source="baostock"):
        if symbol == "000002.SZ":
            raise RuntimeError("失败")
        return pd.DataFrame(), pd.DataFrame()

    monkeypatch.setattr(runner, "fetch_symbol", fake_fetch)
    result = runner.sync_prices(
        conn,
        ["000001.SZ", "000002.SZ", "000003.SZ"],
        "2024-01-01",
        "2024-01-10",
        max_retries=1,
        sleep_seconds=0,
    )
    assert result.total == 3
    assert result.succeeded == 1
    assert result.failed == 1
    assert result.skipped == 1
    text = result.summary()
    assert "总数 3" in text and "跳过 1" in text


def test_last_trade_date_returns_none_for_unknown(conn):
    assert _last_day(conn, "999999.SZ") is None
