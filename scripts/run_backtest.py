"""组合回测入口。

用法：
    python scripts/run_backtest.py --factor momentum_20d
    python scripts/run_backtest.py --factor momentum_20d --horizon 60 --no-save
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.backtest import DEFAULT_COST_BUY, DEFAULT_COST_SELL
from analysis.pipeline import run_backtest
from common.config import load_config
from common.db import session

HEADER = (
    f"{'组合':<12}{'年化收益':>10}{'年化波动':>10}{'夏普':>8}"
    f"{'最大回撤':>10}{'卡玛':>8}{'平均换手':>10}"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="组合回测（含交易成本）")
    parser.add_argument("--factor", default="momentum_20d")
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--groups", type=int, default=5)
    parser.add_argument("--min-listed", type=int, default=60)
    parser.add_argument("--cost-buy", type=float, default=DEFAULT_COST_BUY)
    parser.add_argument("--cost-sell", type=float, default=DEFAULT_COST_SELL)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--no-save", action="store_true", help="不写入 meta_backtest")
    args = parser.parse_args()

    cfg = load_config()
    with session(cfg.database_path) as conn:
        result = run_backtest(
            conn,
            args.factor,
            horizon=args.horizon,
            groups=args.groups,
            min_listed=args.min_listed,
            cost_buy=args.cost_buy,
            cost_sell=args.cost_sell,
            start=args.start,
            end=args.end,
            save=not args.no_save,
        )

    if result.n_periods == 0:
        print("没有可用样本，检查因子是否已计算")
        return 1

    print(
        f"\n因子 {result.factor} ｜ 持有 {args.horizon} 日 ｜ {args.groups} 组 "
        f"｜ 调仓 {result.n_periods} 期"
    )
    print(f"成本：买入 {args.cost_buy:.2%}，卖出 {args.cost_sell:.2%}")
    print("\n" + HEADER)
    for _, row in result.groups.iterrows():
        print(
            f"{row['label']:<12}{row['annual_return']:>9.2%}{row['annual_vol']:>10.2%}"
            f"{row['sharpe']:>8.2f}{row['max_drawdown']:>10.2%}"
            f"{row['calmar']:>8.2f}{row['turnover']:>10.1%}"
        )

    gross = result.long_short["gross"]
    net = result.long_short["net"]
    print(f"\n年化成本拖累：{gross['annual_return'] - net['annual_return']:.2%}")
    print("注：空头在 A 股受融券限制，多空仅作因子有效性的理论度量。")
    if not args.no_save:
        print("已存档到 meta_backtest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
