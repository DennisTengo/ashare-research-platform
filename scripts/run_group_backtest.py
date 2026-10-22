"""因子分组回测入口。

用法：
    python scripts/run_group_backtest.py --factor momentum_20d
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.grouping import (
    TRADING_DAYS_PER_YEAR,
    daily_group_returns,
    is_monotonic,
    long_short,
    summarize,
)
from analysis.pipeline import build_panel
from common.config import load_config
from common.db import session


def main() -> int:
    parser = argparse.ArgumentParser(description="因子分组收益回测")
    parser.add_argument("--factor", default="momentum_20d")
    parser.add_argument("--horizon", type=int, default=20, help="持有交易日数")
    parser.add_argument("--groups", type=int, default=5)
    parser.add_argument("--min-listed", type=int, default=60)
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()

    cfg = load_config()
    with session(cfg.database_path) as conn:
        panel = build_panel(
            conn,
            args.factor,
            horizon=args.horizon,
            groups=args.groups,
            min_listed=args.min_listed,
            start=args.start,
            end=args.end,
        )

    usable = panel.dropna(subset=["fwd_return"])
    if usable.empty:
        print("没有可用样本，检查因子是否已计算")
        return 1

    ppy = max(1, round(TRADING_DAYS_PER_YEAR / args.horizon))
    daily = daily_group_returns(usable)
    stats = summarize(daily, args.groups, ppy)
    spread = long_short(daily, args.groups, ppy)

    print(f"\n因子 {args.factor} ｜ 持有 {args.horizon} 日 ｜ {args.groups} 组")
    print(
        f"样本 {usable['trade_date'].min().date()} ~ {usable['trade_date'].max().date()}"
    )
    print(
        f"调仓 {daily.shape[0]} 期（每 {args.horizon} 个交易日）"
        f" ｜ 每期股票数中位数 {int(usable.groupby('trade_date').size().median())}"
    )

    print("\n各组表现")
    print(f"{'组':<4}{'年化收益':>10}{'年化波动':>10}{'夏普':>8}{'胜率':>8}")
    for _, row in stats.iterrows():
        print(
            f"{row['group']:<4}{row['annual_return']:>9.2%}{row['annual_vol']:>10.2%}"
            f"{row['sharpe']:>8.2f}{row['win_rate']:>8.1%}"
        )

    if spread:
        print("\n多空组合（做多最高组、做空最低组）")
        print(f"  年化收益 {spread['annual_return']:.2%}")
        print(f"  年化波动 {spread['annual_vol']:.2%}")
        print(f"  夏普     {spread['sharpe']:.2f}")
        print(f"  胜率     {spread['win_rate']:.1%}")
        print(f"  累计收益 {spread['cum_return']:.2%}")

    print(f"\n各组年化收益单调：{'是' if is_monotonic(daily, args.groups, ppy) else '否'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
