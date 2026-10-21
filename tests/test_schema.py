"""表结构与连接层的测试。"""

from __future__ import annotations

from common.db import count_rows, table_exists
from warehouse.schema import init_schema


def test_init_schema_creates_all_tables(memory_db):
    init_schema(memory_db)
    for name in (
        "dim_calendar",
        "dim_stock",
        "fact_daily_price",
        "fact_adj_factor",
        "fact_financial",
        "fact_factor",
        "meta_job_log",
        "meta_dq_result",
    ):
        assert table_exists(memory_db, name), f"缺少表 {name}"


def test_init_schema_is_idempotent(memory_db):
    """重复建表不应报错，也不能清空数据。"""
    init_schema(memory_db)
    memory_db.execute("INSERT INTO dim_calendar VALUES ('2024-01-02', TRUE, NULL)")
    init_schema(memory_db)
    assert count_rows(memory_db, "dim_calendar") == 1


def test_primary_key_blocks_duplicate(schema_db):
    """主键约束必须生效，这是幂等写入的前提。"""
    import duckdb
    import pytest

    schema_db.execute("INSERT INTO fact_daily_price VALUES ('600000.SH','2024-01-02',1,1,1,1,100,100.0)")
    with pytest.raises(duckdb.ConstraintException):
        schema_db.execute(
            "INSERT INTO fact_daily_price VALUES ('600000.SH','2024-01-02',2,2,2,2,200,200.0)"
        )


def test_financial_pk_contains_announce_date(schema_db):
    """同一报告期、不同公告日应允许并存（PIT 对齐的基础）。"""
    schema_db.execute(
        "INSERT INTO fact_financial VALUES ('600000.SH','2023-12-31','2024-03-30',1,1,1,1)"
    )
    schema_db.execute(
        "INSERT INTO fact_financial VALUES ('600000.SH','2023-12-31','2024-04-28',2,2,2,2)"
    )
    assert count_rows(schema_db, "fact_financial") == 2


def test_count_rows_rejects_bad_table_name(memory_db):
    import pytest

    with pytest.raises(ValueError):
        count_rows(memory_db, "dim_calendar; DROP TABLE dim_calendar")
