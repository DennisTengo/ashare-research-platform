"""首次同步基础数据：交易日历 + 在市股票列表。

用法：
    .venv\\Scripts\\python.exe scripts\\sync_basic.py
可重复执行，行数不应变化。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collector.calendar import sync_trade_calendar
from collector.stock_list import sync_stock_list
from common.calendar import count_trade_dates, latest_trade_date
from common.config import load_config
from common.db import count_rows, session
from warehouse.schema import init_schema


def main() -> None:
    cfg = load_config()
    print(f"数据库: {cfg.database_path}")

    with session(cfg.database_path) as conn:
        init_schema(conn)
        sync_trade_calendar(conn)
        sync_stock_list(conn)

        print("---- 结果 ----")
        print(f"交易日总数: {count_trade_dates(conn)}")
        print(f"最新交易日: {latest_trade_date(conn)}")
        print(f"股票总数:   {count_rows(conn, 'dim_stock')}")
        print(f"其中 ST:    {conn.execute('SELECT count(*) FROM dim_stock WHERE is_st').fetchone()[0]}")

        print("---- 板块分布 ----")
        rows = conn.execute(
            "SELECT board, count(*) c FROM dim_stock GROUP BY board ORDER BY c DESC"
        ).fetchall()
        for board, c in rows:
            print(f"  {board}: {c}")

        print("---- 抽样 ----")
        rows = conn.execute(
            "SELECT code, name, board FROM dim_stock ORDER BY code LIMIT 5"
        ).fetchall()
        for r in rows:
            print(f"  {r}")


if __name__ == "__main__":
    main()
