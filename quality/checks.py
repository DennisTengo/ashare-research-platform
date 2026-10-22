"""数据质量校验。

对应需求文档 FR-1.6 的六条规则。设计约束：

1. 每条检查只读、只返回问题清单，不修改数据；
2. 涨跌幅一律用**复权价**计算——除权日的原始价大幅下跌是正常现象，
   用原始价会让所有高送转股票全部误报；
3. 单条规则的样本数有上限，避免一次坏数据把内存打满。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from common.logger import get_logger

log = get_logger("quality.checks")

# 各板块单日涨跌幅限制。容差用于覆盖价格四舍五入与停牌复牌等边界情况。
_BOARD_LIMIT = {
    "主板": 0.10,
    "创业板": 0.20,
    "科创板": 0.20,
    "北交所": 0.30,
}
_ST_LIMIT = 0.05
_DEFAULT_LIMIT = 0.10
_TOLERANCE = 0.015

DEFAULT_MAX_ROWS = 500


@dataclass
class Finding:
    """一条校验问题。"""

    rule: str
    severity: str  # error / warning
    code: str | None
    trade_date: date | None
    detail: str


def limit_for(board: str | None, is_st: bool) -> float:
    """返回该股票单日涨跌幅的判定上限（含容差）。"""
    if is_st:
        return _ST_LIMIT + _TOLERANCE
    return _BOARD_LIMIT.get(board or "", _DEFAULT_LIMIT) + _TOLERANCE


def check_price_invalid(
    conn: duckdb.DuckDBPyConnection, max_rows: int = DEFAULT_MAX_ROWS
) -> list[Finding]:
    """规则 2：价格为空、非正，或 OHLC 逻辑矛盾。"""
    rows = conn.execute(
        f"""
        SELECT code, trade_date, open, high, low, close
        FROM fact_daily_price
        WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
           OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
           OR high < low
           OR close > high OR close < low
        ORDER BY trade_date DESC
        LIMIT {int(max_rows)}
        """
    ).fetchall()
    return [
        Finding(
            "price_invalid",
            "error",
            code,
            dt,
            f"O={o} H={h} L={low} C={c}",
        )
        for code, dt, o, h, low, c in rows
    ]


def check_duplicate(
    conn: duckdb.DuckDBPyConnection, max_rows: int = DEFAULT_MAX_ROWS
) -> list[Finding]:
    """规则 3：同一 (code, trade_date) 重复。

    主键本身会拦住重复，这条检查是防御性的——万一有人绕过主键批量导入。
    """
    rows = conn.execute(
        f"""
        SELECT code, trade_date, count(*) AS n
        FROM fact_daily_price
        GROUP BY code, trade_date
        HAVING count(*) > 1
        ORDER BY n DESC
        LIMIT {int(max_rows)}
        """
    ).fetchall()
    return [Finding("duplicate", "error", code, dt, f"重复 {n} 次") for code, dt, n in rows]


def check_factor_monotonic(
    conn: duckdb.DuckDBPyConnection, max_rows: int = DEFAULT_MAX_ROWS
) -> list[Finding]:
    """规则 5：复权因子出现下降。

    真实除权只会让因子抬升，下降只可能是数据噪声或计算错误。
    """
    rows = conn.execute(
        f"""
        SELECT code, trade_date, prev, adj_factor FROM (
            SELECT code, trade_date, adj_factor,
                   lag(adj_factor) OVER (PARTITION BY code ORDER BY trade_date) AS prev
            FROM fact_adj_factor
        )
        WHERE prev IS NOT NULL AND adj_factor < prev - 1e-9
        ORDER BY trade_date DESC
        LIMIT {int(max_rows)}
        """
    ).fetchall()
    return [
        Finding(
            "factor_not_monotonic",
            "error",
            code,
            dt,
            f"{prev:.6f} -> {cur:.6f}（{((cur / prev) - 1) * 100:+.2f}%）",
        )
        for code, dt, prev, cur in rows
    ]


def check_return_limit(
    conn: duckdb.DuckDBPyConnection, max_rows: int = DEFAULT_MAX_ROWS
) -> list[Finding]:
    """规则 1：单日涨跌幅超过板块限制。

    用复权价计算收益率。除权日原始价会跳空，但复权价是连续的，
    因此不会被误判——这是这条规则唯一正确的写法。

    跳过每只股票的前 5 个交易日：全面注册制下新股上市前 5 日不设涨跌幅限制，
    再加上暂停上市后复牌等情形，上市初期的大幅波动是真实交易，不是数据错误。
    """
    rows = conn.execute(
        """
        WITH joined AS (
            SELECT p.code, p.trade_date, s.board, coalesce(s.is_st, FALSE) AS is_st,
                   p.close * f.adj_factor AS adj_close
            FROM fact_daily_price p
            JOIN fact_adj_factor f
              ON p.code = f.code AND p.trade_date = f.trade_date
            LEFT JOIN dim_stock s ON p.code = s.code
        ),
        with_prev AS (
            SELECT *,
                   lag(adj_close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_adj,
                   row_number() OVER (PARTITION BY code ORDER BY trade_date) AS rn
            FROM joined
        )
        SELECT code, trade_date, board, is_st, prev_adj, adj_close,
               adj_close / prev_adj - 1 AS ret
        FROM with_prev
        WHERE rn > 5
          AND prev_adj IS NOT NULL AND prev_adj > 0
          AND abs(adj_close / prev_adj - 1) >
              CASE
                  WHEN is_st THEN 0.05
                  WHEN board IN ('创业板', '科创板') THEN 0.20
                  WHEN board = '北交所' THEN 0.30
                  ELSE 0.10
              END + 0.015
        ORDER BY abs(adj_close / prev_adj - 1) DESC
        LIMIT ?
        """
    , [max_rows]).fetchall()
    return [
        Finding(
            "return_exceeds_limit",
            "warning",
            code,
            dt,
            f"{prev:.2f} -> {cur:.2f}（{ret * 100:+.1f}%），{board or '未知'}",
        )
        for code, dt, board, _is_st, prev, cur, ret in rows
    ]


def check_date_gap(
    conn: duckdb.DuckDBPyConnection, min_missing: int = 5, max_rows: int = DEFAULT_MAX_ROWS
) -> list[Finding]:
    """规则 4：交易日历内出现断档。

    以该股票自身的首末交易日为区间，用日历算应有天数，与实际行数相减。
    缺失天数超过阈值才报——少量缺失可能是停牌或数据源偶发缺失。
    """
    rows = conn.execute(
        f"""
        WITH span AS (
            SELECT code, min(trade_date) AS first_day, max(trade_date) AS last_day
            FROM fact_daily_price
            GROUP BY code
        ),
        expected AS (
            SELECT s.code, count(*) AS n
            FROM span s
            JOIN dim_calendar c
              ON c.is_open AND c.trade_date BETWEEN s.first_day AND s.last_day
            GROUP BY s.code
        ),
        actual AS (
            SELECT code, count(*) AS n FROM fact_daily_price GROUP BY code
        )
        SELECT a.code, e.n - a.n AS missing, s.first_day, s.last_day
        FROM actual a
        JOIN expected e ON e.code = a.code
        JOIN span s ON s.code = a.code
        WHERE e.n - a.n >= {int(min_missing)}
        ORDER BY missing DESC
        LIMIT {int(max_rows)}
        """
    ).fetchall()
    return [
        Finding(
            "date_gap",
            "warning",
            code,
            last_day,
            f"缺失 {missing} 个交易日（{first_day} ~ {last_day}）",
        )
        for code, missing, first_day, last_day in rows
    ]


def check_zero_volume_price_move(
    conn: duckdb.DuckDBPyConnection, max_rows: int = DEFAULT_MAX_ROWS
) -> list[Finding]:
    """规则 6：成交量为 0 但价格发生变化。

    停牌日成交量为 0 且价格不变，属于正常；价格变动才是真异常。
    """
    rows = conn.execute(
        f"""
        WITH x AS (
            SELECT p.code, p.trade_date, p.volume, p.close, p.close * f.adj_factor AS adj_close,
                   lag(p.close * f.adj_factor) OVER (
                       PARTITION BY p.code ORDER BY p.trade_date
                   ) AS prev_adj
            FROM fact_daily_price p
            JOIN fact_adj_factor f
              ON p.code = f.code AND p.trade_date = f.trade_date
        )
        SELECT code, trade_date, close, prev_adj, adj_close
        FROM x
        WHERE volume = 0 AND prev_adj IS NOT NULL AND prev_adj > 0
          AND abs(adj_close / prev_adj - 1) > 1e-4
        ORDER BY trade_date DESC
        LIMIT {int(max_rows)}
        """
    ).fetchall()
    return [
        Finding(
            "zero_volume_price_moved",
            "warning",
            code,
            dt,
            f"量为 0 但复权价 {prev:.4f} -> {cur:.4f}",
        )
        for code, dt, _close, prev, cur in rows
    ]


ALL_CHECKS = (
    check_price_invalid,
    check_duplicate,
    check_factor_monotonic,
    check_return_limit,
    check_date_gap,
    check_zero_volume_price_move,
)


def run_all(
    conn: duckdb.DuckDBPyConnection, max_rows: int = DEFAULT_MAX_ROWS
) -> dict[str, list[Finding]]:
    """依次执行全部检查，返回 {规则名: 问题列表}。

    单条规则出错不影响其他规则——坏数据最容易把查询本身搞崩，
    所以这里必须逐条兜住异常。
    """
    results: dict[str, list[Finding]] = {}
    for check in ALL_CHECKS:
        name = check.__name__.removeprefix("check_")
        try:
            results[name] = check(conn, max_rows=max_rows)
        except Exception as exc:  # noqa: BLE001 - 校验本身不能因为一条规则失败而中断
            log.error("规则 {} 执行失败: {}", name, exc)
            results[name] = []
    return results
