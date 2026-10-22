"""界面与数据层之间的薄封装。

三条约定：
  1. **只读连接**——界面不做任何写入，避免与采集任务抢写锁；
  2. **查询集中在这里**——页面只负责展示，SQL 不散落在各个 page 文件里；
  3. **缓存**——Streamlit 每次交互都会从头重跑脚本，不缓存会反复查库。

注意：duckdb 连接不能被 cache_data 序列化，只能放 cache_resource。
"""

from __future__ import annotations

import duckdb
import pandas as pd
import streamlit as st

from common.config import load_config


@st.cache_resource
def get_conn() -> duckdb.DuckDBPyConnection:
    """全局只读连接。界面永远不写库。"""
    cfg = load_config()
    return duckdb.connect(str(cfg.database_path), read_only=True)


@st.cache_data(ttl=300)
def overview() -> dict:
    """首页看板需要的全部数字。"""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT count(*) AS stocks,
               sum(CASE WHEN list_date IS NOT NULL THEN 1 ELSE 0 END) AS with_date
        FROM dim_stock
        """
    ).fetchone()
    price = conn.execute(
        "SELECT min(trade_date), max(trade_date), count(*) FROM fact_daily_price"
    ).fetchone()
    latest = conn.execute("SELECT max(trade_date) FROM dim_calendar WHERE is_open").fetchone()[0]
    cal = conn.execute("SELECT count(*) FROM dim_calendar WHERE is_open").fetchone()[0]
    fin = conn.execute(
        "SELECT count(*), max(announce_date) FROM fact_financial"
    ).fetchone()
    factors = conn.execute(
        "SELECT factor_name, count(*) FROM fact_factor GROUP BY factor_name ORDER BY factor_name"
    ).fetchall()
    last_job = conn.execute(
        "SELECT job_name, finished_at, status FROM meta_job_log ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return {
        "stocks": row[0],
        "stocks_with_date": row[1],
        "first_day": price[0],
        "last_day": price[1],
        "price_rows": price[2],
        "latest_calendar": latest,
        "calendar_days": cal,
        "financial_rows": fin[0],
        "financial_last_announce": fin[1],
        "factors": factors,
        "last_job": last_job,
    }


@st.cache_data(ttl=300)
def coverage_by_month() -> pd.DataFrame:
    """每月入库行数，用来看数据是均匀增长的还是有断层。"""
    return get_conn().execute(
        """
        SELECT date_trunc('month', trade_date) AS month, count(*) AS rows, count(DISTINCT code) AS codes
        FROM fact_daily_price GROUP BY 1 ORDER BY 1
        """
    ).df()


@st.cache_data(ttl=300)
def stock_options() -> pd.DataFrame:
    """个股查询的下拉候选。"""
    return get_conn().execute(
        "SELECT code, name, board, is_st, list_date FROM dim_stock ORDER BY code"
    ).df()


@st.cache_data(ttl=300)
def price_bars(code: str, start: str, end: str) -> pd.DataFrame:
    """原始价 + 复权因子，复权价在界面层按需换算。

    库里只存不复权价和因子，是项目的硬约束——前复权价永不落库。
    """
    df = get_conn().execute(
        """
        SELECT p.trade_date, p.open, p.high, p.low, p.close, p.volume, p.amount,
               a.adj_factor
        FROM fact_daily_price p
        JOIN fact_adj_factor a ON p.code = a.code AND p.trade_date = a.trade_date
        WHERE p.code = ? AND p.trade_date BETWEEN ? AND ?
        ORDER BY p.trade_date
        """,
        [code, start, end],
    ).df()
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def apply_adjust(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """按复权方式换算价格列。

    mode: raw 不复权 / back 后复权 / forward 前复权
    前复权以区间最后一日的因子为基准，保证最新价格与原始价一致。
    """
    if df.empty or mode == "raw":
        return df
    out = df.copy()
    if mode == "back":
        factor = out["adj_factor"]
    else:
        factor = out["adj_factor"] / out["adj_factor"].iloc[-1]
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * factor
    return out


@st.cache_data(ttl=300)
def financials(code: str) -> pd.DataFrame:
    """财务数据，按报告期降序。公告日一并返回——它决定这期数据哪天才能被看到。"""
    df = get_conn().execute(
        """
        SELECT report_period, announce_date, revenue, net_profit, roe, eps,
               gross_margin, net_margin, extra
        FROM fact_financial WHERE code = ?
        ORDER BY report_period DESC, announce_date DESC
        """,
        [code],
    ).df()
    if not df.empty:
        df["report_period"] = pd.to_datetime(df["report_period"]).dt.date
        df["announce_date"] = pd.to_datetime(df["announce_date"]).dt.date
    return df


@st.cache_data(ttl=300)
def factor_names() -> list[str]:
    return [
        r[0]
        for r in get_conn()
        .execute("SELECT DISTINCT factor_name FROM fact_factor ORDER BY factor_name")
        .fetchall()
    ]


@st.cache_resource
def factor_registry() -> dict:
    """已注册因子的元信息（名称、说明），用于界面展示。"""
    import factor.factors  # noqa: F401  触发自动发现
    from factor.base import all_factors

    return {
        name: {"description": f.description, "lookback": f.lookback, "requires": f.requires}
        for name, f in all_factors().items()
    }
