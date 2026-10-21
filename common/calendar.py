"""交易日历查询。

所有「上一个交易日」「某区间内有哪些交易日」的对齐操作都走这里，
避免各处自己用自然日加减导致错位。
"""

from __future__ import annotations

import datetime as dt

import duckdb


def _as_date(value: dt.date | dt.datetime | str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def is_trade_date(conn: duckdb.DuckDBPyConnection, date) -> bool:
    d = _as_date(date)
    row = conn.execute(
        "SELECT count(*) FROM dim_calendar WHERE trade_date = ? AND is_open", [d]
    ).fetchone()
    return bool(row and row[0])


def prev_trade_date(conn: duckdb.DuckDBPyConnection, date) -> dt.date | None:
    """上一个交易日；若给定日期本身是交易日，返回它之前的那一个。"""
    d = _as_date(date)
    row = conn.execute(
        "SELECT max(trade_date) FROM dim_calendar WHERE trade_date < ? AND is_open", [d]
    ).fetchone()
    return row[0] if row and row[0] else None


def next_trade_date(conn: duckdb.DuckDBPyConnection, date) -> dt.date | None:
    d = _as_date(date)
    row = conn.execute(
        "SELECT min(trade_date) FROM dim_calendar WHERE trade_date > ? AND is_open", [d]
    ).fetchone()
    return row[0] if row and row[0] else None


def latest_trade_date(conn: duckdb.DuckDBPyConnection, as_of=None) -> dt.date | None:
    """已过去的最后一个交易日，默认截至今天。

    数据源会返回已排定的未来交易日，不过滤会让「最新数据日」永远指向未来。
    """
    d = _as_date(as_of) if as_of is not None else dt.date.today()
    row = conn.execute(
        "SELECT max(trade_date) FROM dim_calendar WHERE is_open AND trade_date <= ?", [d]
    ).fetchone()
    return row[0] if row and row[0] else None


def trade_dates_between(conn: duckdb.DuckDBPyConnection, start, end) -> list[dt.date]:
    """闭区间内的全部交易日，升序。"""
    s, e = _as_date(start), _as_date(end)
    rows = conn.execute(
        "SELECT trade_date FROM dim_calendar "
        "WHERE trade_date BETWEEN ? AND ? AND is_open ORDER BY trade_date",
        [s, e],
    ).fetchall()
    return [r[0] for r in rows]


def count_trade_dates(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute("SELECT count(*) FROM dim_calendar WHERE is_open").fetchone()
    return int(row[0]) if row else 0
