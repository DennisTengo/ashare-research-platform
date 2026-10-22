"""因子分组回测入口。

用法：
    python scripts/run_group_backtest.py --factor momentum_20d
    python scripts/run_group_backtest.py --factor momentum_20d --horizon 20 --groups 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from analysis.grouping import (
    TRADING_DAYS_PER_YEAR,
    add_forward_return,
    add_listed_days,
    assign_groups,
    daily_group_returns,
    is_monotonic,
    long_short,
    rebalance_dates,
    summarize,
)
from common.config import load_config
from common.db import session
from factor.engine import load_bars


def load_factor(conn, name: str) -> pd.DataFrame:
    df = conn.execute(
        "SELECT code, trade_date, value AS factor_value FROM fact_factor WHERE factor_name = ?",
        [name],
    ).df()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="因子分组收益回测")
    parser.add_argument("--factor", default="momentum_20d")
    parser.add_argument("--horizon", type=int, default=20, help="持有交易日数")
    parser.add_argument("--groups", type=int, default=5)
    parser.add_argument("--min-listed", type=int, default=60, help="最少上市交易日数")
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()

    cfg = load_config()
    with session(cfg.database_path) as conn:
        bars = load_bars(conn, args.start, args.end)
        if bars.empty:
            print("没有行情数据")
            return 1
        bars = add_forward_return(bars, args.horizon)
        bars = add_listed_days(bars)
        bars["trade_date"] = pd.to_datetime(bars["trade_date"])

        factor = load_factor(conn, args.factor)
        if factor.empty:
            print(f"因子 {args.factor} 没有数据，先运行 compute_factors.py")
            return 1

        panel = bars.merge(factor, on=["code", "trade_date"], how="inner")
        merged_rows = len(panel)
        panel = panel[panel["listed_days"] >= args.min_listed]
        after_listed = len(panel)
        # 不重叠调仓：每 horizon 个交易日只取一个调仓日
        dates = rebalance_dates(panel["trade_date"], args.horizon)
        panel = panel[panel["trade_date"].isin(dates)]
        panel = assign_groups(panel, args.groups)
        usable = panel.dropna(subset=["fwd_return"])

    if usable.empty:
        print("没有可用的样本")
        return 1

    periods_per_year = max(1, round(TRADING_DAYS_PER_YEAR / args.horizon))
    daily = daily_group_returns(usable)
    stats = summarize(daily, args.groups, periods_per_year)
    spread = long_short(daily, args.groups, periods_per_year)

    print(f"\n因子 {args.factor} ｜ 持有 {args.horizon} 日 ｜ {args.groups} 组")
    print(
        f"样本 {usable['trade_date'].min().date()} ~ {usable['trade_date'].max().date()}"
    )
    print(
        f"原始样本 {merged_rows} 条 ｜ 剔除上市不足 {args.min_listed} 日 "
        f"{merged_rows - after_listed} 条 ｜ 非调仓日 {after_listed - len(panel)} 条"
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

    print(f"\n各组年化收益单调：{'是' if is_monotonic(daily, args.groups, periods_per_year) else '否'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
