"""pytest 公共夹具。所有测试不依赖网络与真实数据库文件。"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def memory_db() -> duckdb.DuckDBPyConnection:
    """内存数据库，测试结束自动关闭。"""
    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture()
def schema_db(memory_db):
    """已建好表结构的内存数据库。"""
    from warehouse.schema import init_schema

    init_schema(memory_db)
    return memory_db
