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


@st.cache_data(ttl=3600)
def closed_days(start: str, end: str) -> list[str]:
    """区间内的休市日（含周末与节假日）。

    日历表只存开市日，休市日用补集算：区间内所有自然日减去全市场开市日。
    **不能用个股行情反推**——停牌日也会被算成休市日，那样等于把停牌
    伪装成连续行情，比留空白更危险。
    """
    trading = (
        get_conn()
        .execute(
            "SELECT trade_date FROM dim_calendar "
            "WHERE is_open AND trade_date BETWEEN ? AND ?",
            [start, end],
        )
        .df()["trade_date"]
    )
    if trading.empty:
        return []
    all_days = pd.date_range(start, end, freq="D")
    closed = all_days.difference(pd.to_datetime(trading))
    return [d.strftime("%Y-%m-%d") for d in closed]


def rangebreaks(start: str, end: str) -> list[dict]:
    """Plotly 的 x 轴断点设置。

    周末用规则压缩（bounds），节假日用日期列表（values）。
    分开处理是因为周末占休市日的绝大多数，用规则比列 2000 多个日期快得多。
    只压缩休市日，不碰停牌日——停牌是真实信息，必须留在图上。
    """
    closed = closed_days(start, end)
    workday_closed = [d for d in closed if pd.Timestamp(d).weekday() < 5]
    breaks: list[dict] = [dict(bounds=["sat", "mon"])]
    if workday_closed:
        breaks.append(dict(values=workday_closed))
    return breaks


@st.cache_data(ttl=300)
def missing_trading_days(code: str, start: str, end: str) -> list[str]:
    """区间内全市场开市、但这只股票没数据的日子（停牌或未上市）。

    这些日子在图上故意保留空档——它们是信息，不是噪声。
    """
    rows = (
        get_conn()
        .execute(
            """
            SELECT c.trade_date FROM dim_calendar c
            WHERE c.is_open AND c.trade_date BETWEEN ? AND ?
              AND NOT EXISTS (
                  SELECT 1 FROM fact_daily_price p
                  WHERE p.code = ? AND p.trade_date = c.trade_date
              )
            ORDER BY c.trade_date
            """,
            [start, end, code],
        )
        .fetchall()
    )
    return [str(r[0]) for r in rows]


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
    """财务数据，按报告期降序。公告日一并返回——它决定这期数据哪天才能被看到。

    营收补齐：baostock 只在一季报和三季报之外的期次提供主营收入，
    Q1 与 Q3 的缺失率都是 99%。用 净利润 / 净利率 反推，
    全样本中位误差 0.0006%

    但净利率接近零时，反推会把微小误差放大到无法使用（p95 误差 6.8%），
    所以只在该比值绝对值大于 0.5% 时才补，其余宁可留空。
    补齐的行在 revenue_estimated 列标记，界面必须显示出来。
    """
    df = get_conn().execute(
        """
        SELECT report_period, announce_date, revenue, net_profit, roe, eps,
               gross_margin, net_margin, extra
        FROM fact_financial WHERE code = ?
        ORDER BY report_period DESC, announce_date DESC
        """,
        [code],
    ).df()
    if df.empty:
        return df

    df["report_period"] = pd.to_datetime(df["report_period"]).dt.date
    df["announce_date"] = pd.to_datetime(df["announce_date"]).dt.date

    df["revenue_estimated"] = False
    fillable = (
        df["revenue"].isna()
        & df["net_profit"].notna()
        & df["net_margin"].notna()
        & (df["net_margin"].abs() > 0.005)
    )
    df.loc[fillable, "revenue"] = (
        df.loc[fillable, "net_profit"] / df.loc[fillable, "net_margin"]
    )
    df.loc[fillable, "revenue_estimated"] = True
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
