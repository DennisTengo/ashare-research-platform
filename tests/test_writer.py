"""幂等写入的测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from common.db import count_rows
from warehouse.writer import upsert


def _stock_df(code: str, name: str, board: str = "主板", is_st: bool = False) -> pd.DataFrame:
    return pd.DataFrame({"code": [code], "name": [name], "board": [board], "is_st": [is_st]})


def test_upsert_inserts_new_rows(schema_db):
    n = upsert(schema_db, "dim_stock", _stock_df("600000.SH", "浦发银行"), ["code"])
    assert n == 1
    assert count_rows(schema_db, "dim_stock") == 1


def test_upsert_updates_without_duplicating(schema_db):
    upsert(schema_db, "dim_stock", _stock_df("600000.SH", "浦发银行"), ["code"])
    upsert(schema_db, "dim_stock", _stock_df("600000.SH", "浦发银行A"), ["code"])

    assert count_rows(schema_db, "dim_stock") == 1
    name = schema_db.execute("SELECT name FROM dim_stock WHERE code='600000.SH'").fetchone()[0]
    assert name == "浦发银行A"


def test_upsert_handles_multiple_rows(schema_db):
    df = pd.DataFrame(
        {
            "code": ["600000.SH", "000001.SZ"],
            "name": ["浦发银行", "平安银行"],
            "board": ["主板", "主板"],
            "is_st": [False, False],
        }
    )
    upsert(schema_db, "dim_stock", df, ["code"])
    assert count_rows(schema_db, "dim_stock") == 2


def test_upsert_empty_dataframe_is_noop(schema_db):
    empty = pd.DataFrame(columns=["code", "name", "board", "is_st"])
    assert upsert(schema_db, "dim_stock", empty, ["code"]) == 0
    assert count_rows(schema_db, "dim_stock") == 0


def test_upsert_missing_key_column_raises(schema_db):
    df = pd.DataFrame({"name": ["浦发银行"]})
    with pytest.raises(ValueError, match="主键列"):
        upsert(schema_db, "dim_stock", df, ["code"])


def test_upsert_rejects_unsafe_table_name(schema_db):
    with pytest.raises(ValueError, match="表名"):
        upsert(schema_db, "dim_stock; DROP TABLE dim_stock", _stock_df("600000.SH", "x"), ["code"])


def test_upsert_accepts_nat_for_nullable_date(schema_db):
    """日历第一行的 prev_date 是 NaT，应写成 NULL 而不是报错。"""
    df = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2015-01-05"]),
            "is_open": [True],
            "prev_date": pd.to_datetime([None]),
        }
    )
    upsert(schema_db, "dim_calendar", df, ["trade_date"])
    prev = schema_db.execute("SELECT prev_date FROM dim_calendar").fetchone()[0]
    assert prev is None
