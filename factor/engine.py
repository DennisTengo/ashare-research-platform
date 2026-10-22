"""因子计算引擎。

负责把「因子协议」变成「表里的数据」：读行情、算复权价、按股票分组调用因子、写库。

这里集中处理所有因子共有的麻烦事：
  1. 复权价统一由 close × adj_factor 得出，因子不再自己算；
  2. 按股票分组，保证每个因子的输入是单只股票的连续序列；
  3. 历史不足产生的 NaN 在此过滤，因子函数不需要处理；
  4. 重复计算先删后插，保证幂等。
"""

from __future__ import annotations

import duckdb
import pandas as pd

import factor.factors  # noqa: F401  导入即完成因子注册
from common.logger import get_logger
from factor.base import all_factors, get

log = get_logger("factor.engine")


def load_bars(
    conn: duckdb.DuckDBPyConnection,
    start: str | None = None,
    end: str | None = None,
    financial_fields: tuple[str, ...] = (),
) -> pd.DataFrame:
    """读取日线并计算复权价，按 (code, trade_date) 升序返回。

    financial_fields 非空时，一并将财务字段按公告日对齐到日频（存在 ACID 约束，
    对齐逻辑全在 analysis.financial，这里只转发）。
    """
    if financial_fields:
        # 延迟导入：analysis.financial 与 factor.engine 处于两个包，
        # 顶层互相导入会触发循环初始化。
        from analysis.financial import load_panel_with_financial

        return load_panel_with_financial(conn, tuple(financial_fields), start, end)

    sql = """
        SELECT p.code, p.trade_date, p.close * f.adj_factor AS adj_close
        FROM fact_daily_price p
        JOIN fact_adj_factor f
          ON p.code = f.code AND p.trade_date = f.trade_date
    """
    conditions, params = [], []
    if start:
        conditions.append("p.trade_date >= ?")
        params.append(start)
    if end:
        conditions.append("p.trade_date <= ?")
        params.append(end)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY p.code, p.trade_date"
    return conn.execute(sql, params).df()


def _clear_range(
    conn: duckdb.DuckDBPyConnection,
    name: str,
    start: str | None,
    end: str | None,
) -> None:
    """删除指定因子的旧值，范围与本次计算范围保持一致。

    如果传了 start/end 却按因子名删全表，区间外的历史值会被一并清掉，
    而且不会报错——属于“看起来正常但数据没了”的缺陷。
    """
    if start or end:
        conn.execute(
            "DELETE FROM fact_factor WHERE factor_name = ? "
            "AND trade_date >= ? AND trade_date <= ?",
            [name, start or "1900-01-01", end or "2999-12-31"],
        )
    else:
        conn.execute("DELETE FROM fact_factor WHERE factor_name = ?", [name])


def compute_all(
    conn: duckdb.DuckDBPyConnection,
    factor_names: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, int]:
    """计算并写入指定因子，返回 {因子名: 写入行数}。

    factor_names 为空时计算全部已注册因子。
    """
    names = list(factor_names) if factor_names else sorted(all_factors())
    selected = {name: get(name) for name in names}

    # 汇总本次需要合并的财务字段，一次读入，避免每个因子各查一遍库
    required = tuple(sorted({col for f in selected.values() for col in f.requires}))
    bars = load_bars(conn, start, end, financial_fields=required)
    if bars.empty:
        log.warning("没有可用的行情数据，跳过因子计算")
        return {name: 0 for name in names}

    parts: list[pd.DataFrame] = []
    for code, group in bars.groupby("code", sort=False):
        g = group.reset_index(drop=True)
        for name, factor in selected.items():
            values = factor.fn(g)
            parts.append(
                pd.DataFrame(
                    {
                        "factor_name": name,
                        "trade_date": g["trade_date"],
                        "code": code,
                        "value": values,
                    }
                )
            )

    result = pd.concat(parts, ignore_index=True).dropna(subset=["value"])
    log.info(
        "因子计算完成：{} 个因子，{} 只股票，有效值 {} 个",
        len(names),
        bars["code"].nunique(),
        len(result),
    )

    # 全部股票历史都不足时，结果是空表，也要把旧数据清掉并返回 0
    if result.empty:
        for name in names:
            _clear_range(conn, name, start, end)
        return {name: 0 for name in names}

    counts: dict[str, int] = {}
    for name in names:
        _clear_range(conn, name, start, end)
        sub = result[result["factor_name"] == name][
            ["factor_name", "trade_date", "code", "value"]
        ]
        if sub.empty:
            counts[name] = 0
            continue
        conn.register("_factor_tmp", sub)
        conn.execute(
            "INSERT INTO fact_factor "
            "SELECT CAST(factor_name AS VARCHAR), CAST(trade_date AS DATE), "
            "       CAST(code AS VARCHAR), CAST(value AS DOUBLE) FROM _factor_tmp"
        )
        conn.unregister("_factor_tmp")
        counts[name] = len(sub)
    return counts
