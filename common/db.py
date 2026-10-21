"""DuckDB 连接管理。

约定：业务代码通过 session() 获取连接，不长期持有；写库统一走 warehouse 层。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb


def connect(db_path: str | Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


@contextmanager
def session(db_path: str | Path, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = connect(db_path, read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


def table_exists(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).fetchone()
    return bool(row and row[0])


def count_rows(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    """行数统计。table 来自内部调用，不接受外部输入拼接。"""
    if not table.replace("_", "").isalnum():
        raise ValueError(f"非法表名: {table}")
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0
