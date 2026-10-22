"""IC 分析入口。

用法：
    python scripts/run_ic.py --factor momentum_20d
    python scripts/run_ic.py --factor momentum_20d --decay
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.grouping import TRADING_DAYS_PER_YEAR
from analysis.ic import compute_ic, ic_decay, ic_summary
from analysis.pipeline import build_panel, load_factor
from common.config import load_config
from common.db import session
from factor.engine import load_bars


def main() -> int:
    parser = argparse.ArgumentParser(description="IC 分析")
    parser.add_argument("--factor", default="momentum_20d")
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--min-listed", type=int, default=60)
    parser.add_argument("--min-stocks", type=int, default=30)
    parser.add_argument("--decay", action="store_true", help="输出 IC 衰减曲线")
    parser.add_argument("--decay-horizons", nargs="*", type=int, default=[1, 5, 10, 20, 60])
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
        )
        if panel.empty:
            print("没有可用样本，检查因子是否已计算")
            return 1

        ic = compute_ic(panel, min_stocks=args.min_stocks)

        decay = None
        if args.decay:
            bars = load_bars(conn, args.start, args.end)
            factor = load_factor(conn, args.factor)
            decay = ic_decay(
                bars,
                factor,
                args.decay_horizons,
                min_stocks=args.min_stocks,
                min_listed_days=args.min_listed,
                rebalance_every=args.horizon,
            )

    if ic.empty:
        print("IC 序列为空，检查数据")
        return 1

    ppy = max(1, round(TRADING_DAYS_PER_YEAR / args.horizon))
    stats = ic_summary(ic, ppy)

    print(f"\n因子 {args.factor} ｜ 持有 {args.horizon} 日")
    print(f"IC 期数 {stats['periods']}（每 {args.horizon} 个交易日一期）")
    print(f"  IC 均值   {stats['ic_mean']:+.4f}")
    print(f"  IC 标准差 {stats['ic_std']:.4f}")
    print(f"  ICIR      {stats['icir']:+.3f}（年化 {stats['icir_annual']:+.2f}）")
    print(f"  胜率      {stats['win_rate']:.1%}")
    print(f"  t 值      {stats['t_stat']:+.2f}")

    if decay is not None and not decay.empty:
        print("\nIC 衰减")
        print(f"{'持有期':<8}{'IC均值':>10}{'ICIR':>8}{'胜率':>8}")
        for _, row in decay.iterrows():
            print(
                f"{int(row['horizon']):<8}{row['ic_mean']:>+10.4f}"
                f"{row['icir']:>+8.2f}{row['win_rate']:>8.1%}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
