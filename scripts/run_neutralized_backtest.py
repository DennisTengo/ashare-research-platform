"""双重分组验证：控制一个变量后，因子是否还有效。

用法：
    python scripts/run_neutralized_backtest.py
    python scripts/run_neutralized_backtest.py --factor momentum_20d --layer volatility_20d
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.grouping import (
    TRADING_DAYS_PER_YEAR,
    assign_double_groups,
    is_monotonic,
    layer_group_returns,
    long_short,
    summarize,
)
from analysis.pipeline import build_panel
from common.config import load_config
from common.db import session


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
        panel = build_panel(
            conn,
            args.factor,
            horizon=args.horizon,
            groups=None,
            min_listed=args.min_listed,
            start=args.start,
            end=args.end,
            extra_factors={"layer_value": args.layer},
        )
        if panel.empty:
            print("没有可用样本，检查两个因子是否都已计算")
            return 1

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

    positives = sum(1 for r in summary_rows if (r["spread_annual"] or 0) > 0.02)
    if positives == 0:
        print("\n结论：控制该变量后因子在各层均无显著正收益，原形态很可能是控制变量驱动的。")
    elif positives == len(summary_rows):
        print("\n结论：各层内因子方向一致，说明因子作用独立于控制变量。")
    else:
        print("\n结论：因子只在部分层内有效，作用与控制变量存在交互。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
