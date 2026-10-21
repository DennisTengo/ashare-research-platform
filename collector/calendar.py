"""交易日历采集。

数据源：akshare 的 tool_trade_date_hist_sina，返回历史与未来已知的交易日。
入库时额外计算 prev_date，供后续按「上一个交易日」对齐。
"""

from __future__ import annotations

import duckdb
import pandas as pd

from common.logger import get_logger
from warehouse.writer import upsert

log = get_logger("collector.calendar")


def fetch_trade_calendar() -> pd.DataFrame:
    """拉取交易日历并补齐 is_open / prev_date。"""
    import akshare as ak

    raw = ak.tool_trade_date_hist_sina()
    if raw is None or raw.empty:
        raise RuntimeError("交易日历接口返回空数据")
    if "trade_date" not in raw.columns:
        raise RuntimeError(f"交易日历接口字段变化: {list(raw.columns)}")

    df = pd.DataFrame({"trade_date": pd.to_datetime(raw["trade_date"]).dt.normalize()})
    df = df.drop_duplicates().sort_values("trade_date").reset_index(drop=True)
    df["is_open"] = True
    df["prev_date"] = df["trade_date"].shift(1)
    return df


def sync_trade_calendar(conn: duckdb.DuckDBPyConnection) -> int:
    """全量对齐交易日历，幂等。"""
    df = fetch_trade_calendar()
    rows = upsert(conn, "dim_calendar", df, keys=["trade_date"])
    log.info("交易日历同步完成: {} 行, 区间 {} ~ {}", rows, df["trade_date"].min(), df["trade_date"].max())
    return rows
