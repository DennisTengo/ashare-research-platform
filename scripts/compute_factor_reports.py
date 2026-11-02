"""因子快照报告的计算入口。

用法：
    python scripts/compute_factor_reports.py                 # 只算缺失或过期的
    python scripts/compute_factor_reports.py --force         # 全部重算
    python scripts/compute_factor_reports.py --factor roe    # 只算指定因子
    python scripts/compute_factor_reports.py --start 2021-01-01

幂等靠两枚指纹：参数指纹（horizon/groups/成本/区间）与代码指纹（函数源码）。
两者都没变且上次算成功，直接跳过——重复运行不该重算，也不该改动任何数字。

为什么计算放在这里而不是界面里：
    DuckDB 的库文件是独占锁的。界面进程一旦持有只读连接，另一个进程就连打开都打不开
    （实测报「另一个程序正在使用此文件」），界面进程内部也没法同时开写连接。
    所以写库这条路只能由独立脚本走，界面只读。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import factor.factors  # noqa: F401  导入即完成因子注册
from analysis.factor_report import (
    STATUS_FAILED,
    STATUS_READY,
    build_report,
    compute_params,
    load_reports,
    record_failure,
    save_report,
    status_of,
)
from common.config import load_config
from common.db import session
from common.logger import get_logger
from factor.base import all_factors

log = get_logger("scripts.compute_factor_reports")


def _pick(name: str | None) -> dict:
    factors = all_factors()
    if name:
        if name not in factors:
            raise SystemExit(f"未知因子 {name!r}，已注册：{', '.join(sorted(factors))}")
        return {name: factors[name]}
    return dict(sorted(factors.items()))


def _multi_sharpe(report: dict) -> float | None:
    """列表里给人看的那一条：多空组合（净）的夏普。"""
    for row in report["metrics"]["portfolios"]:
        if row.get("label") == "多空(净)":
            return row.get("sharpe")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="计算因子快照报告并落库")
    parser.add_argument("--factor", help="只算指定因子；不传则算全部已注册因子")
    parser.add_argument("--force", action="store_true", help="忽略指纹，强制重算")
    parser.add_argument("--min-listed", type=int, default=60, help="剔除上市不足 N 个交易日的股票")
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()

    selected = _pick(args.factor)
    cfg = load_config()
    results: list[dict] = []

    with session(cfg.database_path) as conn:
        latest = load_reports(conn)
        for name, factor in selected.items():
            params = compute_params(factor, args.start, args.end, args.min_listed)
            state = status_of(factor, latest.get(name), params)

            if state == STATUS_READY and not args.force:
                print(f"  {name:<18} 已是最新，跳过")
                results.append({"name": name, "status": "跳过", "periods": None, "sharpe": None, "seconds": 0.0})
                continue

            began = time.perf_counter()
            try:
                report = build_report(conn, factor, args.start, args.end, args.min_listed)
            except Exception as exc:  # noqa: BLE001 - 单个因子失败不能中断整批
                message = f"{type(exc).__name__}: {exc}"
                record_failure(conn, factor, message, args.start, args.end, args.min_listed)
                log.error("因子 %s 计算失败：%s", name, message)
                print(f"  {name:<18} 计算失败：{message}")
                results.append({"name": name, "status": STATUS_FAILED, "periods": 0, "sharpe": None,
                                "seconds": time.perf_counter() - began})
                continue

            save_report(conn, report)
            elapsed = time.perf_counter() - began
            results.append(
                {"name": name, "status": STATUS_READY, "periods": report["n_periods"],
                 "sharpe": _multi_sharpe(report), "seconds": elapsed}
            )
            print(
                f"  {name:<18} 完成  {report['n_periods']} 期  "
                f"多空夏普 {_multi_sharpe(report) or 0:>5.2f}  {elapsed:.1f}s"
            )

    done = [r for r in results if r["status"] == STATUS_READY]
    print(
        f"\n共 {len(results)} 个因子：完成 {len(done)}，"
        f"跳过 {sum(1 for r in results if r['status'] == '跳过')}，"
        f"失败 {sum(1 for r in results if r['status'] == STATUS_FAILED)}，"
        f"耗时 {sum(r['seconds'] for r in results):.1f}s"
    )
    return 0 if all(r["status"] != STATUS_FAILED for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())