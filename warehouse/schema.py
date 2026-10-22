"""表结构与初始化。

四张核心表 + 两张元数据表。硬约束：
  1. 价格表只存不复权价，复权因子单独存；
  2. 财务表主键包含公告日，保证 PIT 对齐；
  3. 所有主键显式声明，写入走 upsert 保证幂等。
"""

from __future__ import annotations

import duckdb

DDL_CALENDAR = """
CREATE TABLE IF NOT EXISTS dim_calendar (
    trade_date DATE PRIMARY KEY,
    is_open    BOOLEAN NOT NULL,
    prev_date  DATE
)
"""

DDL_STOCK = """
CREATE TABLE IF NOT EXISTS dim_stock (
    code        VARCHAR PRIMARY KEY,
    name        VARCHAR,
    list_date   DATE,
    delist_date DATE,
    board       VARCHAR,
    industry    VARCHAR,
    is_st       BOOLEAN DEFAULT FALSE,
    updated_at  TIMESTAMP DEFAULT current_timestamp
)
"""

DDL_DAILY_PRICE = """
CREATE TABLE IF NOT EXISTS fact_daily_price (
    code       VARCHAR NOT NULL,
    trade_date DATE    NOT NULL,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    close      DOUBLE,
    volume     BIGINT,
    amount     DOUBLE,
    PRIMARY KEY (code, trade_date)
)
"""

DDL_ADJ_FACTOR = """
CREATE TABLE IF NOT EXISTS fact_adj_factor (
    code       VARCHAR NOT NULL,
    trade_date DATE    NOT NULL,
    adj_factor DOUBLE  NOT NULL,
    PRIMARY KEY (code, trade_date)
)
"""

DDL_FINANCIAL = """
CREATE TABLE IF NOT EXISTS fact_financial (
    code          VARCHAR NOT NULL,
    report_period DATE    NOT NULL,
    announce_date DATE    NOT NULL,
    revenue       DOUBLE,
    net_profit    DOUBLE,
    total_assets  DOUBLE,
    total_equity  DOUBLE,
    roe           DOUBLE,
    eps           DOUBLE,
    gross_margin  DOUBLE,
    net_margin    DOUBLE,
    extra         JSON,
    PRIMARY KEY (code, report_period, announce_date)
)
"""

DDL_FACTOR = """
CREATE TABLE IF NOT EXISTS fact_factor (
    factor_name VARCHAR NOT NULL,
    trade_date  DATE    NOT NULL,
    code        VARCHAR NOT NULL,
    value       DOUBLE,
    PRIMARY KEY (factor_name, trade_date, code)
)
"""

DDL_JOB_LOG = """
CREATE TABLE IF NOT EXISTS meta_job_log (
    job_id      BIGINT PRIMARY KEY,
    job_name    VARCHAR,
    started_at  TIMESTAMP,
    finished_at TIMESTAMP,
    total       INTEGER,
    succeeded   INTEGER,
    failed      INTEGER,
    status      VARCHAR,
    message     VARCHAR
)
"""

DDL_DQ_RESULT = """
CREATE TABLE IF NOT EXISTS meta_dq_result (
    id         BIGINT PRIMARY KEY,
    rule       VARCHAR,
    code       VARCHAR,
    trade_date DATE,
    detail     VARCHAR,
    created_at TIMESTAMP DEFAULT current_timestamp
)
"""

# 回测结果存档。
# params / metrics 用 JSON 而不是拆成列：回测参数和指标会不断新增，
# 拆列意味着每加一个参数就要改表结构，历史记录也会失去可比性。
DDL_BACKTEST = """
CREATE TABLE IF NOT EXISTS meta_backtest (
    id          BIGINT PRIMARY KEY,
    run_at      TIMESTAMP DEFAULT current_timestamp,
    git_commit  VARCHAR,
    factor      VARCHAR,
    label       VARCHAR,
    params      JSON,
    metrics     JSON
)
"""

ALL_DDL = [
    DDL_CALENDAR,
    DDL_STOCK,
    DDL_DAILY_PRICE,
    DDL_ADJ_FACTOR,
    DDL_FINANCIAL,
    DDL_FACTOR,
    DDL_JOB_LOG,
    DDL_DQ_RESULT,
    DDL_BACKTEST,
]


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """幂等建表，可重复执行。"""
    for ddl in ALL_DDL:
        conn.execute(ddl)
