"""双重分组验证：控制一个变量后，因子是否还有效。

典型用途：怀疑动量分层的 U 型是波动率混淆时，先按波动率分三层，
再看每层内部动量的多空收益。如果三层内部多空都接近零，就可以确认
之前的形态由波动率驱动，而不是动量。

用法：
    python scripts/run_neutralized_backtest.py
    python scripts/run_neutralized_backtest.py --factor momentum_20d --layer volatility_20d
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
    assign_double_groups,
    is_monotonic,
    layer_group_returns,
    long_short,
    rebalance_dates,
    summarize,
)
from common.config import load_config
from common.db import session
from factor.engine import load_bars


def load_factor(conn, name: str, as_col: str) -> pd.DataFrame:
    df = conn.execute(
        "SELECT code, trade_date, value AS {} FROM fact_factor WHERE factor_name = ?".format(
            as_col
        ),
        [name],
    ).df()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="控制分层变量后的因子有效性")
    parser.add_argument("--factor", default="momentum_20d")
    parser.add_argument("--layer", default="volatility_20d")
    parser.add_argument("--groups", type=int, default=5)
    parser.add_argument("--layers", type=int, default=3)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--min-listed", type=int, default=60)
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

        fac = load_factor(conn, args.factor, "factor_value")
        lay = load_factor(conn, args.layer, "layer_value")
        if fac.empty or lay.empty:
            print("因子数据缺失，先运行 compute_factors.py")
            return 1

        panel = (
            bars.merge(fac, on=["code", "trade_date"], how="inner")
            .merge(lay, on=["code", "trade_date"], how="inner")
        )
        panel = panel[panel["listed_days"] >= args.min_listed]
        dates = rebalance_dates(panel["trade_date"], args.horizon)
        panel = panel[panel["trade_date"].isin(dates)]
        panel = assign_double_groups(panel, args.groups, args.layers)
        usable = panel.dropna(subset=["fwd_return"])

    if usable.empty:
        print("没有可用样本")
        return 1

    ppy = max(1, round(TRADING_DAYS_PER_YEAR / args.horizon))
    by_layer = layer_group_returns(usable)

    print(f"\n因子 {args.factor} ｜ 控制变量 {args.layer} ｜ 持有 {args.horizon} 日")
    print(f"{args.layers} 层 × {args.groups} 组 ｜ 调仓 {usable['trade_date'].nunique()} 期")
    print("（层号 1 表示该控制变量最小，例如低波动）")

    summary_rows = []
    for layer in sorted(by_layer.index.get_level_values(0).unique()):
        daily = by_layer.loc[layer]
        stats = summarize(daily, args.groups, ppy)
        spread = long_short(daily, args.groups, ppy)
        mono = is_monotonic(daily, args.groups, ppy)

        print(f"\n—— 第 {layer} 层 ——")
        print(f"{'组':<4}{'年化收益':>10}{'年化波动':>10}{'夏普':>8}")
        for _, row in stats.iterrows():
            print(
                f"{row['group']:<4}{row['annual_return']:>9.2%}"
                f"{row['annual_vol']:>10.2%}{row['sharpe']:>8.2f}"
            )
        if spread:
            print(
                f"多空：年化 {spread['annual_return']:>7.2%}  "
                f"夏普 {spread['sharpe']:>5.2f}  胜率 {spread['win_rate']:.1%}"
            )
        print(f"组间单调：{'是' if mono else '否'}")

        summary_rows.append(
            {
                "layer": layer,
                "spread_annual": spread.get("annual_return"),
                "spread_sharpe": spread.get("sharpe"),
                "monotonic": mono,
            }
        )

    print("\n—— 各层多空对比 ——")
    print(f"{'层':<4}{'多空年化':>10}{'夏普':>8}{'单调':>6}")
    for row in summary_rows:
        annual = row["spread_annual"]
        sharpe = row["spread_sharpe"]
        print(
            f"{row['layer']:<4}"
            f"{(f'{annual:.2%}' if annual is not None else '-'):>10}"
            f"{(f'{sharpe:.2f}' if sharpe is not None else '-'):>8}"
            f"{('是' if row['monotonic'] else '否'):>6}"
        )

    positives = sum(
        1 for r in summary_rows if (r["spread_annual"] or 0) > 0.02
    )
    if positives == 0:
        print("\n结论：控制该变量后因子在各层均无显著正收益，原形态很可能是控制变量驱动的。")
    elif positives == len(summary_rows):
        print("\n结论：各层内因子方向一致，说明因子作用独立于控制变量。")
    else:
        print("\n结论：因子只在部分层内有效，作用与控制变量存在交互。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
