"""初始化数据库表结构。

幂等操作，可重复执行。新增表之后跑一次即可，不影响已有数据。

用法：
    python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import load_config
from common.db import session
from warehouse.schema import init_schema


def main() -> int:
    cfg = load_config()
    with session(cfg.database_path) as conn:
        init_schema(conn)
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
    print(f"数据库: {cfg.database_path}")
    print(f"共 {len(tables)} 张表:")
    for (name,) in tables:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
