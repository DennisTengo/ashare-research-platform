"""一键启动：环境检查 → 数据增量更新 → 启动界面。

用法：
    python run.py                # 检查 + 增量更新 + 启动界面
    python run.py --skip-update  # 跳过更新，直接启动（调试界面时用）
    python run.py --port 8600    # 换端口

对应需求 FR-7.3。增量更新会先跑基础数据（日历 + 股票列表）再跑行情，
两者都是幂等的，重复执行不会产生重复行。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check_env() -> list[str]:
    """启动前的硬性检查。返回问题列表，空列表表示通过。"""
    problems: list[str] = []

    for module in ("pandas", "duckdb", "streamlit", "plotly", "pydantic", "yaml"):
        try:
            __import__(module)
        except ImportError:
            problems.append(f"缺少依赖 {module}，执行 pip install -e .")

    from common.config import load_config

    cfg = load_config()
    db = cfg.database_path
    if not db.exists():
        problems.append(f"数据库不存在：{db}，先运行 python scripts/sync_basic.py 与 sync_prices.py")
        return problems

    import duckdb

    try:
        conn = duckdb.connect(str(db), read_only=True)
    except Exception as exc:  # noqa: BLE001 - 写锁占用是最常见原因
        problems.append(f"数据库无法打开（可能有采集任务在写）：{exc}")
        return problems

    try:
        for table in ("dim_calendar", "dim_stock", "fact_daily_price", "fact_factor"):
            n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if n == 0:
                problems.append(f"表 {table} 为空，先同步基础数据")
        latest = conn.execute("SELECT max(trade_date) FROM fact_daily_price").fetchone()[0]
        if latest:
            print(f"  行情最新日期：{latest}")
    finally:
        conn.close()

    return problems


def run_step(name: str, args: list[str]) -> bool:
    """执行一个子脚本，失败不阻塞后续（界面仍可用旧数据启动）。"""
    print(f"\n[{name}] 开始")
    result = subprocess.run([sys.executable, *args], cwd=ROOT)
    if result.returncode != 0:
        print(f"[{name}] 失败（返回码 {result.returncode}），继续启动界面")
        return False
    print(f"[{name}] 完成")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="一键启动投研平台")
    parser.add_argument("--skip-update", action="store_true", help="跳过数据更新")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()

    print("---- 环境检查 ----")
    problems = check_env()
    if problems:
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("  通过")

    if not args.skip_update:
        ok = run_step("基础数据", ["scripts/sync_basic.py"])
        if ok:
            run_step("行情增量", ["scripts/sync_prices.py"])

    print(f"\n---- 启动界面 http://localhost:{args.port} ----")
    # headless 必须显式开启：首次运行 Streamlit 会弹出交互式邮箱询问并阻塞，
    # 在脚本里启动时表现为“卡在启动阶段、健康检查一直失败”。
    env = {
        **os.environ,
        "STREAMLIT_SERVER_HEADLESS": "true",
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
    }
    return subprocess.run(
        [
            sys.executable, "-m", "streamlit", "run", str(ROOT / "app" / "main.py"),
            "--server.port", str(args.port),
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ],
        cwd=ROOT,
        env=env,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
