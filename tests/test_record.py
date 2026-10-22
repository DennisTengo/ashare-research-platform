"""回测存档的单元测试。"""

from __future__ import annotations

import duckdb
import pytest

from analysis.record import current_commit, load_history, record_backtest
from warehouse.schema import init_schema


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def test_record_and_load_roundtrip(conn):
    params = {"horizon": 20, "groups": 5, "cost_buy": 0.0013}
    results = [
        {"label": "G1", "annual_return": 0.12, "sharpe": 0.8},
        {"label": "G5", "annual_return": 0.25, "sharpe": 0.75},
    ]
    record_id = record_backtest(conn, "momentum_20d", "默认", params, results)

    history = load_history(conn, "momentum_20d")
    assert len(history) == 1
    assert history[0]["id"] == record_id
    assert history[0]["params"] == params
    assert history[0]["metrics"]["results"] == results


def test_multiple_records_are_ordered_newest_first(conn):
    record_backtest(conn, "f1", "a", {}, [])
    record_backtest(conn, "f1", "b", {}, [])
    history = load_history(conn, "f1")
    assert [h["label"] for h in history] == ["b", "a"]


def test_history_filters_by_factor(conn):
    record_backtest(conn, "momentum_20d", "a", {}, [])
    record_backtest(conn, "volatility_20d", "b", {}, [])
    assert len(load_history(conn, "momentum_20d")) == 1
    assert len(load_history(conn)) == 2


def test_params_can_grow_without_schema_change(conn):
    """参数集合变化时不需要改表——这正是用 JSON 的原因。"""
    record_backtest(conn, "f1", "v1", {"horizon": 20}, [])
    record_backtest(conn, "f1", "v2", {"horizon": 60, "new_param": "x"}, [])
    history = load_history(conn, "f1")
    assert history[0]["params"]["new_param"] == "x"
    assert history[1]["params"] == {"horizon": 20}


def test_current_commit_returns_string():
    commit = current_commit()
    assert isinstance(commit, str) and commit
