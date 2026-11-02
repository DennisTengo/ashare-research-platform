"""交易成本：把一次调仓拆成逐笔成交，按方向分别计费。

为什么单独一层：
    需求 FR-6.4 要求「佣金、印花税、滑点三项可配置」，FR-6.6 要求能查
    当日交易明细。这两条都指向同一件事——成本必须是**逐笔可追溯**的，
    而不是一个笼统的百分比。

旧口径（`analysis.backtest.portfolio_returns`）的三个缺陷，本模块逐个修正：
    1. 只按「新进名单占比」收费，退出的股票没有卖出费；
    2. 首期按「换手 1.0 × (买入费 + 卖出费)」收，但建仓并不需要卖出；
    3. 等权组合每期都要把漂移后的权重拉回等权，这部分再平衡成本完全没算。
    三处都让成本偏乐观，换手越高的因子偏得越多（DEVLOG 039）。

费率拆成三项而不是两个固定数：
    买入 = 佣金 + 滑点
    卖出 = 佣金 + 印花税 + 滑点
    印花税只在卖出收，是 A 股和多数市场的主要差异。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# 权重变化小于这个值视为没成交。等权 60 只时单只权重 0.0167，
# 1e-9 足够收紧，不会把真实的 0.01% 调仓误判成噪音。
WEIGHT_TOL = 1e-9


@dataclass(frozen=True)
class CostModel:
    """三项费率。默认值来自 config.backtest，界面直接显示这三个数字。

    费率为「占成交金额的比例」，例如佣金 0.0003 即万三。
    """

    commission: float = 0.0003
    stamp_tax: float = 0.001
    slippage: float = 0.001

    @property
    def buy_rate(self) -> float:
        return self.commission + self.slippage

    @property
    def sell_rate(self) -> float:
        return self.commission + self.stamp_tax + self.slippage

    @classmethod
    def from_config(cls, path: str | None = None) -> CostModel:
        """从配置文件读默认值。配置缺失时退回类默认值。"""
        from common.config import load_config

        bt = load_config(path).backtest
        return cls(
            commission=bt.commission_rate,
            stamp_tax=bt.stamp_tax,
            slippage=bt.slippage,
        )

    def as_dict(self) -> dict:
        return {
            "commission": self.commission,
            "stamp_tax": self.stamp_tax,
            "slippage": self.slippage,
            "buy_rate": self.buy_rate,
            "sell_rate": self.sell_rate,
        }


def rebalance_trade(
    before: pd.Series,
    after: pd.Series,
    model: CostModel,
    tol: float = WEIGHT_TOL,
) -> pd.DataFrame:
    """把「调仓前权重 → 调仓后权重」拆成逐笔成交。

    before / after 是 Series，index 为股票代码、值为持仓权重，各自合计 1。
    两只股票权重相同但代码不同，就是一次换仓：一只卖出、一只买入，
    两笔都收费——这正是旧口径漏掉的一半。

    返回列：code / w_before / w_after / delta / side / cost。
    权重没有变化的股票不出现（不是成交）。
    """
    codes = before.index.union(after.index)
    before_w = before.reindex(codes).fillna(0.0)
    after_w = after.reindex(codes).fillna(0.0)
    delta = after_w - before_w

    moved = delta[delta.abs() > tol]
    if moved.empty:
        return pd.DataFrame(
            columns=["code", "w_before", "w_after", "delta", "side", "cost"]
        )

    side = pd.Series("买入", index=moved.index)
    side[moved < 0] = "卖出"
    rate = side.map({"买入": model.buy_rate, "卖出": model.sell_rate})

    return pd.DataFrame(
        {
            "code": moved.index,
            "w_before": before_w.reindex(moved.index).values,
            "w_after": after_w.reindex(moved.index).values,
            "delta": moved.values,
            "side": side.values,
            "cost": (moved.abs() * rate).values,
        }
    )


def rebalance_cost(
    before: pd.Series,
    after: pd.Series,
    model: CostModel,
    tol: float = WEIGHT_TOL,
) -> dict:
    """一次调仓的成本汇总，附带可追溯的逐笔明细。

    换手按**单边**定义：成交金额占净值比例。首期建仓是全额买入、没有卖出，
    所以首期换手为 1.0 而不是 1.0 的一半。之后买卖两侧理论上相等，
    取较大者是为了吸收权重合计的浮点误差。

    首期建仓时 before 为空，结果是「全额买入、没有卖出」，
    与旧口径的「买入费 + 卖出费」不同，这是本模块有意修正的第二条缺陷。
    """
    trades = rebalance_trade(before, after, model, tol)
    if trades.empty:
        return {
            "turnover": 0.0,
            "buy_turnover": 0.0,
            "sell_turnover": 0.0,
            "cost_buy": 0.0,
            "cost_sell": 0.0,
            "cost": 0.0,
            "n_trades": 0,
            "trades": trades,
        }

    buy = trades[trades["side"] == "买入"]
    sell = trades[trades["side"] == "卖出"]
    buy_turnover = float(buy["delta"].sum())
    sell_turnover = float(-sell["delta"].sum())
    cost_buy = float(buy["cost"].sum())
    cost_sell = float(sell["cost"].sum())

    return {
        # 单边换手：买入额与卖出额理论相等，首期只有买入（建仓），取大者更符合直觉
        "turnover": max(buy_turnover, sell_turnover),
        "buy_turnover": buy_turnover,
        "sell_turnover": sell_turnover,
        "cost_buy": cost_buy,
        "cost_sell": cost_sell,
        "cost": cost_buy + cost_sell,
        "n_trades": int(len(trades)),
        "trades": trades,
    }


def drift_weights(weights: pd.Series, returns: pd.Series) -> pd.Series:
    """持仓在持有期末的权重。

    等权建仓之后，涨得多的股票权重自然变大，下一期要买回等权就得
    卖掉涨多的、买入涨少的——这部分换手是真实成交，必须计费。

    returns 缺失时按 0 处理（该股这一期没有可观测收益），调用方负责标注
    「待核算」，不要把它当成「成本为零」。
    """
    if weights.empty:
        return weights
    r = returns.reindex(weights.index).fillna(0.0)
    grown = weights * (1.0 + r)
    total = float(grown.sum())
    if total <= 0:
        return weights
    return grown / total