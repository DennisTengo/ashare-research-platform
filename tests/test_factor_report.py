"""因子快照报告的单元测试。

两类关注点：
    1. 指纹：参数变了、代码变了必须能看出来，说明改了不该触发重算。
    2. 口径：年度分解复利拼接必须等于全样本累计，曲线与指标必须逐期对齐。
       这条不变量一旦破了，页面上的两处数字就会互相打脸，而且没人能解释。

全部跑在内存库的合成数据上（60 只股票 × 40 个交易日），不碰真实数据。
"""

from __future__ import annotations

import json

import duckdb
import pandas as pd
import pytest

from analysis.factor_report import (
    STATUS_FAILED,
    STATUS_MISSING,
    STATUS_READY,
    STATUS_STALE,
    _dumps,
    build_report,
    code_hash,
    compute_params,
    load_reports,
    params_hash,
    record_failure,
    save_report,
    source_code,
    status_of,
)
from factor.base import Factor, all_factors, register
from warehouse.schema import init_schema

N_STOCKS = 60
N_DAYS = 40


def _demo(bars: pd.DataFrame) -> pd.Series:
    """模块级函数，inspect.getsource 才拿得到源码。"""
    return bars["adj_close"].pct_change(3)


def _seed(conn) -> None:
    dates = pd.bdate_range("2024-01-01", periods=N_DAYS)
    stocks, prices, adj, values = [], [], [], []
    for i in range(N_STOCKS):
        code = f"{i + 1:06d}.SZ"
        stocks.append((code, f"股{i}", "主板", False))
        for j, d in enumerate(dates):
            close = 10.0 * (1.0 + 0.002 * i * j)
            prices.append((code, d.date(), close, close, close, close, 1000, close * 1000))
            adj.append((code, d.date(), 1.0))
            values.append(("demo_factor", d.date(), code, float(i)))
    conn.executemany("INSERT INTO dim_stock (code, name, board, is_st) VALUES (?, ?, ?, ?)", stocks)
    conn.executemany(
        "INSERT INTO fact_daily_price "
        "(code, trade_date, open, high, low, close, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        prices,
    )
    conn.executemany(
        "INSERT INTO fact_adj_factor (code, trade_date, adj_factor) VALUES (?, ?, ?)", adj
    )
    conn.executemany(
        "INSERT INTO fact_factor (factor_name, trade_date, code, value) VALUES (?, ?, ?, ?)",
        values,
    )


@pytest.fixture()
def conn():
    c = duckdb.connect(":memory:")
    init_schema(c)
    _seed(c)
    yield c
    c.close()


@pytest.fixture()
def demo() -> Factor:
    return Factor(name="demo_factor", description="演示", lookback=4, fn=_demo, horizon=5, groups=5)


# ---------------------------------------------------------------- 参数与指纹


def test_factor_settings_defaults_and_declaration(demo):
    bare = Factor(name="bare", description="x", lookback=1, fn=_demo)
    assert (bare.horizon, bare.groups) == (20, 5)
    assert bare.settings == {"horizon": 20, "groups": 5}
    assert demo.settings == {"horizon": 5, "groups": 5}


def test_params_hash_is_order_independent():
    a = {"horizon": 20, "groups": 5, "cost_buy": 0.0013}
    b = {"cost_buy": 0.0013, "groups": 5, "horizon": 20}
    assert params_hash(a) == params_hash(b)


def test_params_hash_changes_with_horizon(demo):
    params = compute_params(demo)
    other = dict(params, horizon=params["horizon"] + 1)
    assert params_hash(params) != params_hash(other)


def test_compute_params_carries_cost_and_assumptions(demo):
    params = compute_params(demo)
    # 成本从配置文件推导：买入 = 佣金 + 滑点，卖出再加印花税
    assert params["cost_buy"] == pytest.approx(params["commission"] + params["slippage"])
    assert params["cost_sell"] == pytest.approx(
        params["commission"] + params["stamp_tax"] + params["slippage"]
    )
    # 实现上的假设必须和数字一起存，否则对不上账时说不清
    assert "T+1 收盘" in params["trade_price"]
    assert params["universe"]


def test_code_hash_ignores_description_but_not_lookback(demo):
    same = Factor(name="demo_factor", description="换个说法", lookback=4, fn=_demo, horizon=5)
    assert code_hash(same) == code_hash(demo)
    changed = Factor(name="demo_factor", description="演示", lookback=9, fn=_demo, horizon=5)
    assert code_hash(changed) != code_hash(demo)


def test_source_code_reads_function_body(demo):
    text = source_code(demo)
    assert "pct_change" in text


def test_register_rejects_bad_horizon_and_groups():
    with pytest.raises(ValueError, match="horizon"):
        register(Factor(name="bad_h", description="x", lookback=1, fn=_demo, horizon=0))
    with pytest.raises(ValueError, match="groups"):
        register(Factor(name="bad_g", description="x", lookback=1, fn=_demo, groups=1))
    assert "bad_h" not in all_factors() and "bad_g" not in all_factors()


# ---------------------------------------------------------------- 报告结构


def test_build_report_structure(conn, demo):
    report = build_report(conn, demo, min_listed=1)
    assert report["status"] == STATUS_READY
    assert report["factor_name"] == "demo_factor"
    assert report["params_hash"] == params_hash(report["params"])
    assert set(report["yearly"]) == {"多空(净)", "多头(净)", "基准(等权)", "IC"}
    assert report["metrics"]["first_date"] <= report["metrics"]["last_date"]


def test_curve_arrays_are_aligned(conn, demo):
    report = build_report(conn, demo, min_listed=1)
    curve = report["curve"]
    n = len(curve["dates"])
    assert n > 0
    assert report["n_periods"] == n
    for series in curve["pnl"].values():
        assert len(series) == n
    assert len(curve["turnover"]) == n
    assert len(curve["ic"]) == n
    assert len(curve["rolling_sharpe"]) == n
    # 滚动夏普窗口是一年，期数不够时前面留空而不是填 0
    assert curve["rolling_sharpe"][0] is None


def test_periods_match_across_portfolios(conn, demo):
    """期数只有一个口径：curve 的点数、绩效表里的 periods 必须一致。"""
    report = build_report(conn, demo, min_listed=1)
    for row in report["metrics"]["portfolios"]:
        assert row["periods"] == report["n_periods"]


def test_yearly_compounds_to_full_sample(conn, demo):
    """年度收益复利拼接 == 曲线末端累计。年度表与全样本必须同一套公式。"""
    report = build_report(conn, demo, min_listed=1)
    for label, rows in report["yearly"].items():
        if label == "IC":
            continue
        compounded = 1.0
        for row in rows:
            compounded *= 1.0 + row["total_return"]
        assert compounded == pytest.approx(report["curve"]["pnl"][label][-1], rel=1e-6)


def test_report_is_strict_json(conn, demo):
    """落库前必须能写成合法 JSON：NaN 不是合法 JSON，读回来会炸。"""
    report = build_report(conn, demo, min_listed=1)
    for key in ("params", "metrics", "yearly", "curve"):
        json.loads(_dumps(report[key]))


def test_empty_sample_raises(conn, demo):
    missing = Factor(name="no_such_factor", description="x", lookback=4, fn=_demo, horizon=5)
    with pytest.raises(ValueError, match="没有可回测的样本"):
        build_report(conn, missing, min_listed=1)


# ---------------------------------------------------------------- 落库与状态


def test_save_and_load_roundtrip(conn, demo):
    report = build_report(conn, demo, min_listed=1)
    save_report(conn, report)
    loaded = load_reports(conn)["demo_factor"]
    assert loaded["params"]["horizon"] == demo.horizon
    assert loaded["n_periods"] == report["n_periods"]
    assert len(loaded["curve"]["dates"]) == len(report["curve"]["dates"])
    assert loaded["metrics"]["ic"]["periods"] == report["metrics"]["ic"]["periods"]


def test_save_is_idempotent(conn, demo):
    report = build_report(conn, demo, min_listed=1)
    save_report(conn, report)
    save_report(conn, report)
    count = conn.execute(
        "SELECT count(*) FROM meta_factor_report WHERE factor_name = 'demo_factor'"
    ).fetchone()[0]
    assert count == 1


def test_status_transitions(conn, demo):
    # 注意：参数指纹只认「同一组参数」。这里用 min_listed=1 与档案对齐，
    # 否则比的是两套口径，永远对不上。
    params = compute_params(demo, min_listed=1)
    assert status_of(demo, None, params) == STATUS_MISSING

    report = build_report(conn, demo, min_listed=1)
    save_report(conn, report)
    latest = load_reports(conn)["demo_factor"]
    assert status_of(demo, latest, params) == STATUS_READY

    # 参数变了：同一个档案不再代表当前口径
    assert status_of(demo, latest, compute_params(demo)) == STATUS_STALE


def test_status_detects_code_change(conn, demo):
    params = compute_params(demo, min_listed=1)
    save_report(conn, build_report(conn, demo, min_listed=1))
    latest = load_reports(conn)["demo_factor"]
    assert status_of(demo, latest, params) == STATUS_READY

    def _changed(bars: pd.DataFrame) -> pd.Series:
        return bars["adj_close"].pct_change(2)

    edited = Factor(name="demo_factor", description="演示", lookback=4, fn=_changed,
                    horizon=5, groups=5)
    assert status_of(edited, latest, params) == STATUS_STALE


def test_record_failure_keeps_reason(conn, demo):
    record_failure(conn, demo, "ValueError: 没有可回测的样本", min_listed=1)
    latest = load_reports(conn)["demo_factor"]
    assert latest["status"] == STATUS_FAILED
    assert "没有可回测的样本" in latest["error"]
    assert status_of(demo, latest, compute_params(demo)) == STATUS_FAILED