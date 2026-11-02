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

# 因子快照报告。
# 与 meta_backtest 的区别：那张表存「某次回测跑出什么」，同一因子可以有很多条；
# 这张表存「这个因子的档案」，一个 (因子, 参数) 只应有一条，重算就覆盖。
# 主键含 params_hash 而不是只靠 factor_name：改了 horizon/groups 或代码，
# 就是另一份档案，旧的那份得留着，否则「上周那个数怎么来的」永远说不清。
DDL_FACTOR_REPORT = """
CREATE TABLE IF NOT EXISTS meta_factor_report (
    factor_name VARCHAR NOT NULL,
    params_hash VARCHAR NOT NULL,
    status      VARCHAR NOT NULL,
    code_hash   VARCHAR,
    params      JSON,
    metrics     JSON,
    yearly      JSON,
    curve       JSON,
    n_periods   INTEGER,
    computed_at TIMESTAMP,
    error       VARCHAR,
    PRIMARY KEY (factor_name, params_hash)
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
    DDL_FACTOR_REPORT,
]


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """幂等建表，可重复执行。"""
    for ddl in ALL_DDL:
        conn.execute(ddl)
