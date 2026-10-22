"""财务数据的日频对齐层。

财报是季度频率，因子需要每天一个值。对齐规则只有一条：

    某一天能看到的财务数据 = 公告日不晚于该日的最近一期报告。

这条规则决定了因子不会偷看未来——2024Q1 的报告期是 3-31，但要到 4 月下旬
才公告，在 4-15 就用它的数字等于提前半个月知道答案。实测 300 只沪深300
的公告滞后中位数 38 天，最长 485 天（次新股上市时补披露），用报告期对齐
会系统性地提前一个月以上。

本模块同时负责财务派生指标（同比增速）的计算。理由：同比涉及"分母为负
怎么办"这类口径判断，属于财务语义，不该散落在各个因子函数里。
"""

from __future__ import annotations

import duckdb
import pandas as pd

from common.logger import get_logger

log = get_logger("analysis.financial")

# 可直接从 fact_financial 取的字段，白名单形式，防止拼接外部输入
DIRECT_FIELDS: dict[str, str] = {
    "roe": "f.roe",
    "net_profit": "f.net_profit",
    "revenue": "f.revenue",
    "net_margin": "f.net_margin",
    "gross_margin": "f.gross_margin",
    "eps": "f.eps",
}

# 需要额外计算的派生字段
DERIVED_FIELDS = ("net_profit_yoy", "roe_annualized")

AVAILABLE_FIELDS = tuple(DIRECT_FIELDS) + DERIVED_FIELDS

# baostock 的 roeAvg 是年内累计口径（Q1 是一季度、Q4 是全年），
# 直接使用会造成季节性伪信号：每年 4 月一季报一出，所有股票 ROE 集体“跳水”三分之四。
# 按季度数年化是消除季节性最简单的做法。对强季节性行业仍有偏差，但比不处理好得多。
_ROE_ANNUALIZED_EXPR = """
    CASE WHEN f.roe IS NULL THEN NULL
         WHEN month(f.report_period) = 3 THEN f.roe * 4.0
         WHEN month(f.report_period) = 6 THEN f.roe * 2.0
         WHEN month(f.report_period) = 9 THEN f.roe * 4.0 / 3.0
         ELSE f.roe
    END AS roe_annualized
"""

# 同比增速：分母为负时无意义（负转正、亏损缩小都无法用百分比表达），置空而不是硬算。
_YOY_EXPR = """
    CASE WHEN prev.net_profit > 0 AND f.net_profit IS NOT NULL
         THEN f.net_profit / prev.net_profit - 1.0
    END AS net_profit_yoy
"""

_DERIVED_EXPRS = (_ROE_ANNUALIZED_EXPR, _YOY_EXPR)


def _select_list(fields: tuple[str, ...]) -> str:
    unknown = [f for f in fields if f not in AVAILABLE_FIELDS]
    if unknown:
        raise ValueError(f"未知财务字段: {unknown}，可选：{AVAILABLE_FIELDS}")
    parts = [DIRECT_FIELDS[f] for f in fields if f in DIRECT_FIELDS]
    parts.extend(f"fin.{d}" for d in DERIVED_FIELDS if d in fields)
    return ", ".join(parts)


def load_panel_with_financial(
    conn: duckdb.DuckDBPyConnection,
    fields: tuple[str, ...],
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """读取行情并合并按公告日对齐后的财务字段。

    返回列：code, trade_date, adj_close, 以及请求的财务字段。
    没有可用财报的交易日，财务列为 NaN（例如次新股上市初期）。
    """
    if not fields:
        raise ValueError("至少请求一个财务字段")

    where, params = [], []
    if start:
        where.append("p.trade_date >= ?")
        params.append(start)
    if end:
        where.append("p.trade_date <= ?")
        params.append(end)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        WITH base AS (
            SELECT p.code, p.trade_date, p.close * a.adj_factor AS adj_close
            FROM fact_daily_price p
            JOIN fact_adj_factor a
              ON p.code = a.code AND p.trade_date = a.trade_date
            {where_sql}
        ),
        fin AS (
            SELECT f.code, f.report_period, f.announce_date, f.roe, f.net_profit,
                   f.revenue, f.net_margin, f.gross_margin, f.eps,
                   {', '.join(_DERIVED_EXPRS)}
            FROM fact_financial f
            LEFT JOIN fact_financial prev
              ON prev.code = f.code
             AND prev.report_period = f.report_period - INTERVAL 1 YEAR
        )
        SELECT b.code, b.trade_date, b.adj_close, {_select_list(fields)}
        FROM base b
        ASOF LEFT JOIN (SELECT * FROM fin ORDER BY code, announce_date) fin
          ON b.code = fin.code AND b.trade_date >= fin.announce_date
        ORDER BY b.code, b.trade_date
    """
    df = conn.execute(sql, params).df()
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def coverage(conn: duckdb.DuckDBPyConnection, factor_values: pd.DataFrame) -> dict:
    """统计财务字段的实际覆盖情况，用于判断因子是否被数据缺口拖累。

    财务因子最容易出的问题不是算错，而是「大部分股票没有值」却没人发现。
    """
    if factor_values.empty:
        return {"rows": 0, "codes": 0}
    return {
        "rows": int(len(factor_values)),
        "codes": int(factor_values["code"].nunique()),
        "dates": int(factor_values["trade_date"].nunique()),
    }
