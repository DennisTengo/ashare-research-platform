"""财务数据采集入口。

用法：
    python scripts/sync_financial.py --limit 5          # 先跑 5 只验证
    python scripts/sync_financial.py                    # 全部有行情的股票
    python scripts/sync_financial.py --codes 600519.SH 000001.SZ
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import financial
from collector.financial import quarter_end
from collector.sources import baostock_logout
from common.config import load_config
from common.db import session
from common.logger import get_logger

log = get_logger("scripts.sync_financial")


def existing_periods(conn, symbol: str) -> set[str]:
    rows = conn.execute(
        "SELECT report_period FROM fact_financial WHERE code = ?", [symbol]
    ).fetchall()
    return {r[0].isoformat() for r in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="采集财务数据")
    parser.add_argument("--codes", nargs="*", help="指定股票代码，留空表示全部有行情的股票")
    parser.add_argument("--limit", type=int, help="只处理前 N 只，用于验证")
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=dt.date.today().year)
    parser.add_argument("--sleep", type=float, default=0.3, help="每只股票之间的间隔秒数")
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    years = list(range(args.start_year, args.end_year + 1))
    started = dt.datetime.now()
    cfg = load_config()

    with session(cfg.database_path) as conn:
        if args.codes:
            symbols = args.codes
        else:
            symbols = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT code FROM fact_daily_price ORDER BY code"
                ).fetchall()
            ]
        if args.limit:
            symbols = symbols[: args.limit]

        print(f"待处理 {len(symbols)} 只股票，年度范围 {args.start_year}~{args.end_year}")

        succeeded = failed = skipped = total_rows = 0
        failures: list[tuple[str, str]] = []

        for index, symbol in enumerate(symbols, start=1):
            existing = existing_periods(conn, symbol)
            todo = [
                (y, q)
                for y in years
                for q in financial.QUARTERS
                if quarter_end(y, q) not in existing
            ]
            if not todo:
                skipped += 1
            else:
                try:
                    records = []
                    for year, quarter in todo:
                        record = financial.fetch_quarter(symbol, year, quarter)
                        if record:
                            records.append(record)
                    total_rows += financial.save(conn, records)
                    succeeded += 1
                except Exception as exc:  # noqa: BLE001 - 单只失败不影响整批
                    failed += 1
                    failures.append((symbol, str(exc)))
                    log.warning("{} 财务数据采集失败: {}", symbol, exc)

            if args.progress_every and index % args.progress_every == 0:
                print(
                    f"  进度 {index}/{len(symbols)} ｜ 成功 {succeeded} "
                    f"跳过 {skipped} 失败 {failed} ｜ 写入 {total_rows} 行"
                )
            if args.sleep:
                time.sleep(args.sleep)

        baostock_logout()
        rows_in_db = conn.execute("SELECT count(*) FROM fact_financial").fetchone()[0]
        covered = conn.execute(
            "SELECT count(DISTINCT code) FROM fact_financial"
        ).fetchone()[0]

    elapsed = (dt.datetime.now() - started).total_seconds()
    print("\n---- 结果 ----")
    print(
        f"总数 {len(symbols)}，成功 {succeeded}，跳过 {skipped}，失败 {failed}，"
        f"写入 {total_rows} 行"
    )
    print(f"fact_financial: {rows_in_db} 行，覆盖 {covered} 只股票")
    print(f"耗时 {elapsed/60:.1f} 分钟")
    for symbol, message in failures[:5]:
        print(f"  失败: {symbol} — {message}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
