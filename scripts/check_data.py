"""数据质量校验入口。

用法：
    python scripts/check_data.py                  # 打印报告
    python scripts/check_data.py --write          # 同时写入 meta_dq_result
    python scripts/check_data.py --sample 10      # 每条规则多显示几条样本
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import load_config
from common.db import session
from quality.checks import DEFAULT_MAX_ROWS, run_all
from quality.report import format_report, save_findings


def main() -> int:
    parser = argparse.ArgumentParser(description="对入库数据执行六条质量规则")
    parser.add_argument("--write", action="store_true", help="写入 meta_dq_result 表")
    parser.add_argument("--sample", type=int, default=3, help="每条规则展示的样本数")
    parser.add_argument(
        "--max-rows", type=int, default=DEFAULT_MAX_ROWS, help="单条规则最多返回的问题数"
    )
    args = parser.parse_args()

    cfg = load_config()
    with session(cfg.database_path) as conn:
        results = run_all(conn, max_rows=args.max_rows)
        print(format_report(results, sample=args.sample))
        if args.write:
            count = save_findings(conn, results)
            print(f"\n已写入 meta_dq_result：{count} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
