"""组合回测入口：带交易成本的净值曲线。

用法：
    python scripts/run_backtest.py --factor momentum_20d
    python scripts/run_backtest.py --factor volatility_20d --cost-buy 0 --cost-sell 0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from analysis.backtest import (
    DEFAULT_COST_BUY,
    DEFAULT_COST_SELL,
    long_short_returns,
    performance,
    portfolio_returns,
)
from analysis.record import record_backtest
from analysis.grouping import (
    TRADING_DAYS_PER_YEAR,
    add_forward_return,
    add_listed_days,
    assign_groups,
    rebalance_dates,
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


def _row(perf: dict, turnover: float) -> str:
    return (
        f"{perf['label']:<12}{perf['annual_return']:>10.2%}{perf['annual_vol']:>10.2%}"
        f"{perf['sharpe']:>8.2f}{perf['max_drawdown']:>10.2%}"
        f"{perf['calmar']:>8.2f}{turnover:>10.1%}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="组合回测（含交易成本）")
    parser.add_argument("--factor", default="momentum_20d")
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--groups", type=int, default=5)
    parser.add_argument("--min-listed", type=int, default=60)
    parser.add_argument("--cost-buy", type=float, default=DEFAULT_COST_BUY)
    parser.add_argument("--cost-sell", type=float, default=DEFAULT_COST_SELL)
    parser.add_argument("--no-save", action="store_true", help="不写入 meta_backtest")
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()

    cfg = load_config()
    with session(cfg.database_path) as conn:
        bars = load_bars(conn, args.start, args.end)
        if bars.empty:
            print("没有行情数据")
            return 1
        factor = load_factor(conn, args.factor)
        if factor.empty:
            print(f"因子 {args.factor} 无数据")
            return 1

        frame = add_forward_return(bars, args.horizon)
        frame = add_listed_days(frame)
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        panel = frame.merge(factor, on=["code", "trade_date"], how="inner")
        panel = panel[panel["listed_days"] >= args.min_listed]
        dates = rebalance_dates(panel["trade_date"], args.horizon)
        panel = panel[panel["trade_date"].isin(dates)]
        panel = assign_groups(panel, args.groups)

    if panel.empty:
        print("没有可用样本")
        return 1

    ppy = max(1, round(TRADING_DAYS_PER_YEAR / args.horizon))
    print(
        f"\n因子 {args.factor} ｜ 持有 {args.horizon} 日 ｜ {args.groups} 组 "
        f"｜ 调仓 {panel['trade_date'].nunique()} 期"
    )
    print(f"成本：买入 {args.cost_buy:.2%}，卖出 {args.cost_sell:.2%}")

    header = (
        f"{'组合':<12}{'年化收益':>10}{'年化波动':>10}{'夏普':>8}"
        f"{'最大回撤':>10}{'卡玛':>8}{'平均换手':>10}"
    )
    records: list[dict] = []

    def _emit(perf: dict, turnover: float) -> str:
        records.append({**perf, "turnover": turnover})
        return _row(perf, turnover)

    # 基准：全部股票等权，不扣成本
    bench = portfolio_returns(panel, None, 0.0, 0.0)
    bench_perf = performance(bench["net"], ppy, label="基准(等权)")
    print("\n" + header)
    print(_emit(bench_perf, bench["turnover"].mean()))

    for g in range(1, args.groups + 1):
        result = portfolio_returns(panel, g, args.cost_buy, args.cost_sell)
        perf = performance(result["net"], ppy, label=f"G{g}")
        print(_emit(perf, result["turnover"].mean()))

    ls = long_short_returns(panel, args.groups, args.cost_buy, args.cost_sell)
    gross_perf = performance(ls["gross"], ppy, label="多空(毛)")
    net_perf = performance(ls["net"], ppy, label="多空(净)")
    print("\n多空组合（做多最高组、做空最低组）")
    print(_emit(gross_perf, ls["turnover"].mean()))
    print(_emit(net_perf, ls["turnover"].mean()))
    print(f"年化成本拖累：{gross_perf['annual_return'] - net_perf['annual_return']:.2%}")
    print("\n注：空头在 A 股受融券限制，多空仅作因子有效性的理论度量。")

    if not args.no_save:
        with session(cfg.database_path) as save_conn:
            record_id = record_backtest(
                save_conn,
                args.factor,
                "默认参数",
                {
                    "horizon": args.horizon,
                    "groups": args.groups,
                    "cost_buy": args.cost_buy,
                    "cost_sell": args.cost_sell,
                    "min_listed": args.min_listed,
                    "start": args.start,
                    "end": args.end,
                },
                records,
            )
        print(f"已存档：record id {record_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
