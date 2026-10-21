"""日线行情同步入口。

示例：
    .venv\\Scripts\\python.exe scripts\\sync_prices.py --universe hs300 --start 2020-01-01
    .venv\\Scripts\\python.exe scripts\\sync_prices.py --universe hs300 --start 2020-01-01 --limit 5
    .venv\\Scripts\\python.exe scripts\\sync_prices.py --universe all --start 2015-01-01 --source akshare
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collector.runner import sync_prices, write_job_log
from collector.sources import baostock_logout
from collector.universe import resolve_universe
from common.config import load_config
from common.db import count_rows, session
from warehouse.schema import init_schema


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步日线行情与复权因子")
    parser.add_argument("--universe", default="hs300", help="股票池：hs300 / all")
    parser.add_argument("--start", default="2020-01-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束日期，默认今天")
    parser.add_argument("--source", default=None, help="数据源：baostock / akshare，默认取配置")
    parser.add_argument("--batch-size", type=int, default=50, help="每多少只休眠一次")
    parser.add_argument("--sleep", type=float, default=1.0, help="批间休眠秒数")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只，用于快速验证")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config()
    end = args.end or dt.date.today().isoformat()
    source = args.source or cfg.source.primary
    started = dt.datetime.now()

    print(f"数据库: {cfg.database_path}")
    print(f"股票池: {args.universe}  数据源: {source}  区间: {args.start} ~ {end}")

    try:
        with session(cfg.database_path) as conn:
            init_schema(conn)
            symbols = resolve_universe(args.universe, conn)
            if args.limit:
                symbols = symbols[: args.limit]
            print(f"待处理标的: {len(symbols)} 只")

            result = sync_prices(
                conn,
                symbols,
                args.start,
                end,
                batch_size=args.batch_size,
                sleep_seconds=args.sleep,
                max_retries=cfg.source.max_retries,
                source=source,
            )
            job_id = write_job_log(conn, f"sync_prices:{args.universe}", started, result)

            print("---- 结果 ----")
            print(result.summary())
            print(f"job_id: {job_id}")
            print(f"fact_daily_price: {count_rows(conn, 'fact_daily_price')} 行")
            print(f"fact_adj_factor:  {count_rows(conn, 'fact_adj_factor')} 行")

            if result.failures:
                print("---- 失败样本（前 10） ----")
                for symbol, error in result.failures[:10]:
                    print(f"  {symbol}: {error[:120]}")

            row = conn.execute(
                "SELECT min(trade_date), max(trade_date), count(DISTINCT code) FROM fact_daily_price"
            ).fetchone()
            print(f"覆盖区间: {row[0]} ~ {row[1]}，股票数 {row[2]}")
    finally:
        baostock_logout()

    return 0 if result.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
