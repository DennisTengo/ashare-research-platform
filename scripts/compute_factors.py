"""计算因子并写入 fact_factor。

用法：
    python scripts/compute_factors.py                      # 全部因子
    python scripts/compute_factors.py --factors momentum_20d
    python scripts/compute_factors.py --start 2022-01-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import factor.factors  # noqa: F401  触发注册
from collector.runner import SyncResult, write_job_log
from common.config import load_config
from common.db import session
from factor.base import all_factors
from factor.engine import compute_all


def main() -> int:
    parser = argparse.ArgumentParser(description="计算因子值")
    parser.add_argument("--factors", nargs="*", help="因子名，留空表示全部")
    parser.add_argument("--start", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    args = parser.parse_args()

    if args.factors:
        unknown = [n for n in args.factors if n not in all_factors()]
        if unknown:
            print(f"未知因子: {unknown}")
            print(f"可用: {sorted(all_factors())}")
            return 1

    cfg = load_config()
    started = dt.datetime.now()
    with session(cfg.database_path) as conn:
        counts = compute_all(conn, args.factors, args.start, args.end)
        result = SyncResult(
            total=len(counts),
            succeeded=sum(1 for n in counts.values() if n > 0),
            rows=sum(counts.values()),
        )
        write_job_log(conn, "compute_factors", started, result)
        print("\n---- 结果 ----")
        for name, n in counts.items():
            print(f"  {name}: {n} 行")
        total = conn.execute("SELECT count(*) FROM fact_factor").fetchone()[0]
        print(f"fact_factor 合计: {total} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
