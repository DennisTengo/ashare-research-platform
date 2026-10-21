"""批量采集执行器。

职责：分批、限速、重试、断点续跑、任务日志。
断点续跑按「该股票在库中的最大交易日」判断，而不是按行数。
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field

import duckdb

from collector.daily_price import fetch_symbol, save
from common.logger import get_logger

log = get_logger("collector.runner")


@dataclass
class SyncResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    rows: int = 0
    skipped: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"总数 {self.total}，成功 {self.succeeded}，跳过 {self.skipped}，"
            f"失败 {self.failed}，写入 {self.rows} 行"
        )


def last_trade_date(conn: duckdb.DuckDBPyConnection, symbol: str) -> dt.date | None:
    row = conn.execute(
        "SELECT max(trade_date) FROM fact_daily_price WHERE code = ?", [symbol]
    ).fetchone()
    return row[0] if row and row[0] else None


def _next_day(d: dt.date) -> dt.date:
    return d + dt.timedelta(days=1)


def sync_prices(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    start: str,
    end: str,
    batch_size: int = 50,
    sleep_seconds: float = 1.0,
    max_retries: int = 3,
    progress_every: int = 10,
    source: str = "baostock",
) -> SyncResult:
    """逐只同步日线与复权因子。单只失败不影响整批。"""
    result = SyncResult(total=len(symbols))
    end_date = dt.date.fromisoformat(end)

    for index, symbol in enumerate(symbols, start=1):
        begin_date = dt.date.fromisoformat(start)
        last = last_trade_date(conn, symbol)
        if last is not None:
            begin_date = max(begin_date, _next_day(last))

        if begin_date > end_date:
            result.skipped += 1
        else:
            last_error: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    prices, factors = fetch_symbol(
                        symbol, begin_date.isoformat(), end_date.isoformat(), source=source
                    )
                    result.rows += save(conn, prices, factors)
                    result.succeeded += 1
                    last_error = None
                    break
                except Exception as exc:  # noqa: BLE001 - 外部接口异常需全部兜住
                    last_error = exc
                    if attempt < max_retries:
                        time.sleep(sleep_seconds * attempt)
            if last_error is not None:
                result.failed += 1
                result.failures.append((symbol, str(last_error)))
                log.warning("{} 采集失败（已重试 {} 次）: {}", symbol, max_retries, last_error)

        if progress_every and index % progress_every == 0:
            log.info(
                "进度 {}/{} ｜ {} ｜ 累计 {} 行",
                index,
                result.total,
                result.summary(),
                result.rows,
            )
        if sleep_seconds and index % max(batch_size, 1) == 0:
            time.sleep(sleep_seconds)

    return result


def write_job_log(
    conn: duckdb.DuckDBPyConnection,
    job_name: str,
    started_at: dt.datetime,
    result: SyncResult,
) -> int:
    """记录一次采集任务，返回 job_id。"""
    job_id = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO meta_job_log "
        "(job_id, job_name, started_at, finished_at, total, succeeded, failed, status, message) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            job_id,
            job_name,
            started_at,
            dt.datetime.now(),
            result.total,
            result.succeeded,
            result.failed,
            "partial" if result.failed else "success",
            result.summary(),
        ],
    )
    return job_id
