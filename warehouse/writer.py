"""幂等写入。

所有入库操作都经过这里。重复执行同一批数据，行数不变、值被更新。
这是「断点续跑」与「重复抓取不出脏数据」的基础。
"""

from __future__ import annotations

from typing import Sequence

import duckdb
import pandas as pd


def _check_identifier(name: str, kind: str) -> str:
    """表名与列名只允许字母数字下划线，防止拼接注入。"""
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"非法{kind}: {name!r}")
    return name


def upsert(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    df: pd.DataFrame,
    keys: Sequence[str],
    staging_view: str = "_upsert_stage",
) -> int:
    """按主键写入：存在则更新非主键列，不存在则插入。

    Args:
        conn: DuckDB 连接。
        table: 目标表名，必须在 schema.py 中定义过。
        df: 待写入数据，列名需与目标表一致，允许只包含部分列。
        keys: 主键列，必须存在于 df 中。
        staging_view: 临时视图名，同一连接内复用需避免并发。

    Returns:
        尝试写入的行数（等于 df 行数）。

    Raises:
        ValueError: 表名/列名非法，或 df 缺少主键列。
    """
    _check_identifier(table, "表名")
    if df is None or df.empty:
        return 0

    missing = [k for k in keys if k not in df.columns]
    if missing:
        raise ValueError(f"DataFrame 缺少主键列: {missing}")

    _check_identifier(staging_view, "视图名")
    for col in df.columns:
        _check_identifier(col, "列名")

    update_cols = [c for c in df.columns if c not in keys]
    key_clause = ", ".join(keys)

    conn.register(staging_view, df)
    try:
        if update_cols:
            set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
            sql = (
                f"INSERT INTO {table} BY NAME SELECT * FROM {staging_view} "
                f"ON CONFLICT ({key_clause}) DO UPDATE SET {set_clause}"
            )
        else:
            sql = (
                f"INSERT INTO {table} BY NAME SELECT * FROM {staging_view} "
                f"ON CONFLICT ({key_clause}) DO NOTHING"
            )
        conn.execute(sql)
    finally:
        conn.unregister(staging_view)

    return len(df)
