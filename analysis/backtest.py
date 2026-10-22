"""组合回测：把分组结果变成带交易成本的净值曲线。

与分组回测的区别：
    分组回测算的是「每一组每期的平均收益」，零成本、假设满仓全额成交，
    回答的是「因子有没有区分度」。
    组合回测模拟「每期调仓、按换手扣成本」，回报一。    
    回答的是「按这个信号实际交易能不能赚钱」。

成本模型（A 股现实值）：
    买入：佣金万三 + 滑点，约 0.13%
    卖出：佣金万三 + 印花税千一 + 滑点，约 0.23%
    每期成本 = 换手率 × (买入成本 + 卖出成本)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common.constants import DEFAULT_COST_BUY, DEFAULT_COST_SELL, TRADING_DAYS_PER_YEAR


def portfolio_returns(
    panel: pd.DataFrame,
    target_group: int | None = None,
    cost_buy: float = DEFAULT_COST_BUY,
    cost_sell: float = DEFAULT_COST_SELL,
) -> pd.DataFrame:
    """模拟每期等权持有目标组，返回逐期收益与换手。

    target_group 为 None 时持有全部股票（作为基准）。

    换手率定义为「本期新买入的股票占本期持仓的比例」。
    等权组合下，这个比例乘以双边成本就是当期的成本损耗。
    """
    rows = []
    prev: set[str] = set()
    for date, day in panel.groupby("trade_date", sort=True):
        holdings = day if target_group is None else day[day["group"] == target_group]
        holdings = holdings.dropna(subset=["fwd_return"])
        if holdings.empty:
            continue
        codes = set(holdings["code"])
        turnover = 1.0 if not prev else len(codes - prev) / len(codes)
        gross = holdings["fwd_return"].mean()
        cost = turnover * (cost_buy + cost_sell)
        rows.append(
            {
                "trade_date": date,
                "n_holdings": len(codes),
                "gross": gross,
                "turnover": turnover,
                "cost": cost,
                "net": gross - cost,
            }
        )
        prev = codes
    return pd.DataFrame(rows).set_index("trade_date")


def long_short_returns(
    panel: pd.DataFrame,
    n_groups: int = 5,
    cost_buy: float = DEFAULT_COST_BUY,
    cost_sell: float = DEFAULT_COST_SELL,
) -> pd.DataFrame:
    """多空组合：多头最高组、空头最低组，两边各自扣成本。

    空头在中国市场实际操作受限（融券成本高、券源少），这里只作为因子有效性的
    理论度量，不代表可执行策略。
    """
    long = portfolio_returns(panel, n_groups, cost_buy, cost_sell)
    short = portfolio_returns(panel, 1, cost_buy, cost_sell)
    out = pd.DataFrame(
        {
            "gross": long["gross"] - short["gross"],
            "cost": long["cost"] + short["cost"],
            "turnover": (long["turnover"] + short["turnover"]) / 2,
        }
    )
    out["net"] = out["gross"] - out["cost"]
    return out


def max_drawdown(nav: pd.Series) -> float:
    """最大回撤，返回负数（例如 -0.35 表示回撤 35%）。"""
    if nav.empty:
        return float("nan")
    peak = nav.cummax()
    return float((nav / peak - 1.0).min())


def drawdown_series(nav: pd.Series) -> pd.Series:
    """由净值序列算逐期回撤序列，用于画图。"""
    if nav.empty:
        return nav
    return nav / nav.cummax() - 1.0


def performance(
    returns: pd.Series,
    periods_per_year: float = 12,
    risk_free: float = 0.0,
    label: str = "",
) -> dict:
    """从每期收益序列计算绩效指标。"""
    clean = returns.dropna()
    n = len(clean)
    if n == 0:
        return {"label": label, "periods": 0}
    nav = (1.0 + clean).cumprod()
    total = float(nav.iloc[-1] - 1.0)
    annual = (1.0 + total) ** (periods_per_year / n) - 1.0 if total > -1 else -1.0
    vol = float(clean.std() * np.sqrt(periods_per_year))
    mdd = max_drawdown(nav)
    return {
        "label": label,
        "periods": n,
        "total_return": total,
        "annual_return": annual,
        "annual_vol": vol,
        "sharpe": (annual - risk_free) / vol if vol else float("nan"),
        "max_drawdown": mdd,
        "calmar": annual / abs(mdd) if mdd else float("nan"),
        "win_rate": float((clean > 0).mean()),
    }


def nav_series(returns: pd.Series) -> pd.Series:
    """累计净值序列，起点为 1.0。"""
    clean = returns.dropna()
    if clean.empty:
        return pd.Series(dtype=float)
    nav = (1.0 + clean).cumprod()
    return pd.concat([pd.Series([1.0], index=[clean.index[0] - pd.Timedelta(days=1)]), nav])
