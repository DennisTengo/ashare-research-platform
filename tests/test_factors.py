"""因子框架的单元测试。

重点验证两件事：因子算法本身对不对，以及引擎的边界处理（历史不足、复权对齐）。
所有用例都用内存库和手工构造的数据，不依赖网络和真实数据库。
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

import factor.factors  # noqa: F401  触发注册
from factor.base import Factor, all_factors, get, register
from factor.engine import compute_all
from warehouse.schema import init_schema


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def _insert_series(conn, code, closes, factor=1.0):
    """按收盘价序列插入日线与复权因子，日期为连续自然日。"""
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    for d, close in zip(dates, closes):
        conn.execute(
            "INSERT INTO fact_daily_price (code, trade_date, open, high, low, close, volume, amount) "
            "VALUES (?, ?, ?, ?, ?, ?, 1000, ?)",
            [code, d.date(), close, close, close, close, close * 1000],
        )
        conn.execute(
            "INSERT INTO fact_adj_factor (code, trade_date, adj_factor) VALUES (?, ?, ?)",
            [code, d.date(), factor],
        )


def test_registry_contains_three_factors():
    names = set(all_factors())
    assert {"momentum_20d", "reversal_20d", "volatility_20d"} <= names


def test_registry_rejects_duplicate_name():
    dup = Factor(name="momentum_20d", description="重复", lookback=5, fn=lambda b: b["adj_close"])
    with pytest.raises(ValueError, match="重名"):
        register(dup)


def test_get_unknown_factor_lists_available():
    with pytest.raises(KeyError, match="未知因子"):
        get("not_exist_factor")


def test_momentum_value_is_exact(conn):
    """20 天从 10 涨到 30，动量应为 +200%。"""
    _insert_series(conn, "000001.SZ", [10 + i for i in range(21)])
    compute_all(conn, ["momentum_20d"])
    value = conn.execute(
        "SELECT value FROM fact_factor WHERE factor_name='momentum_20d' AND code='000001.SZ'"
    ).fetchone()[0]
    assert value == pytest.approx(2.0)


def test_reversal_is_negative_momentum(conn):
    _insert_series(conn, "000001.SZ", [10 + i for i in range(21)])
    compute_all(conn, ["momentum_20d", "reversal_20d"])
    rows = dict(
        conn.execute(
            "SELECT factor_name, value FROM fact_factor WHERE code='000001.SZ'"
        ).fetchall()
    )
    assert rows["reversal_20d"] == pytest.approx(-rows["momentum_20d"])


def test_volatility_of_constant_growth_is_zero(conn):
    """每日固定 +1%，波动率应为 0。"""
    closes = [10.0 * (1.01**i) for i in range(30)]
    _insert_series(conn, "000001.SZ", closes)
    compute_all(conn, ["volatility_20d"])
    value = conn.execute(
        "SELECT max(value) FROM fact_factor WHERE factor_name='volatility_20d'"
    ).fetchone()[0]
    assert value == pytest.approx(0.0, abs=1e-10)


def test_insufficient_history_yields_no_rows(conn):
    """只有 10 天数据，不足 21 天窗口，不应写入任何因子值。"""
    _insert_series(conn, "000001.SZ", [10 + i for i in range(10)])
    counts = compute_all(conn, ["momentum_20d"])
    assert counts["momentum_20d"] == 0


def test_first_valid_row_appears_at_window_end(conn):
    """21 个交易日只能产生 1 个动量值，出现在最后一天。"""
    _insert_series(conn, "000001.SZ", [10 + i for i in range(21)])
    counts = compute_all(conn, ["momentum_20d"])
    assert counts["momentum_20d"] == 1


def test_engine_handles_multiple_symbols(conn):
    _insert_series(conn, "000001.SZ", [10 + i for i in range(21)])
    _insert_series(conn, "000002.SZ", [20 + i for i in range(21)])
    counts = compute_all(conn, ["momentum_20d"])
    assert counts["momentum_20d"] == 2
    codes = {
        r[0]
        for r in conn.execute(
            "SELECT code FROM fact_factor WHERE factor_name='momentum_20d'"
        ).fetchall()
    }
    assert codes == {"000001.SZ", "000002.SZ"}


def test_recompute_is_idempotent(conn):
    """重复计算不应产生重复行。"""
    _insert_series(conn, "000001.SZ", [10 + i for i in range(21)])
    compute_all(conn, ["momentum_20d"])
    compute_all(conn, ["momentum_20d"])
    total = conn.execute("SELECT count(*) FROM fact_factor").fetchone()[0]
    assert total == 1


def test_partial_compute_preserves_history(conn):
    """只算一个区间时，区间外的历史因子值不能被删掉。"""
    _insert_series(conn, "000001.SZ", [10 + i for i in range(40)])
    compute_all(conn, ["momentum_20d"])
    before = conn.execute("SELECT count(*) FROM fact_factor").fetchone()[0]
    assert before > 5

    # 只重算后段：早于 start 的因子值应当保留
    compute_all(conn, ["momentum_20d"], start="2024-01-25")
    early = conn.execute(
        "SELECT count(*) FROM fact_factor WHERE trade_date < '2024-01-25'"
    ).fetchone()[0]
    assert early > 0, "区间外的历史因子值被误删"


def test_adjusted_price_is_used_not_raw(conn):
    """复权因子非 1 时，因子应基于复权价计算。"""
    # 原始价恒定 10，因子从 1 涨到 2：复权价实际翻倍，动量应为 +100%
    dates = pd.date_range("2024-01-01", periods=21, freq="D")
    for i, d in enumerate(dates):
        conn.execute(
            "INSERT INTO fact_daily_price (code, trade_date, open, high, low, close, volume, amount) "
            "VALUES ('000001.SZ', ?, 10, 10, 10, 10, 1000, 10000)",
            [d.date()],
        )
        conn.execute(
            "INSERT INTO fact_adj_factor (code, trade_date, adj_factor) VALUES ('000001.SZ', ?, ?)",
            [d.date(), 1.0 if i < 10 else 2.0],
        )
    compute_all(conn, ["momentum_20d"])
    value = conn.execute("SELECT value FROM fact_factor").fetchone()[0]
    assert value == pytest.approx(1.0)
