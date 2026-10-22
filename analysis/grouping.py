"""因子分组分析。

回答问题：把股票按因子值排序分组后，各组未来的收益有没有差异。

所有函数都是纯计算，不碰数据库——IO 由 scripts 层负责，这样逻辑可以单独测。

时点约定（防止未来函数）：
    T 日收盘算出因子值 → T+1 日收盘买入 → T+1+H 日收盘卖出
    因子只用到 T 日及以前的数据，收益从 T+1 开始，两者不重叠。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def add_forward_return(bars: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    """追加未来收益列。bars 需含 code / trade_date / adj_close。

    fwd_return = close[T+1+H] / close[T+1] - 1，持有 H 个交易日。
    """
    out = bars.sort_values(["code", "trade_date"]).copy()
    g = out.groupby("code", sort=False)["adj_close"]
    out["fwd_return"] = g.shift(-(horizon + 1)) / g.shift(-1) - 1.0
    return out


def add_listed_days(bars: pd.DataFrame) -> pd.DataFrame:
    """追加已上市交易日数（从该股票在库中的第一条记录起算）。

    注意：这不是真实上市天数，因为 dim_stock.list_date 目前为空。
    对 2020 年后上市的股票准确，对更早上市的老股会低估——但用于剔除
    “上市初期极端值”已经够用，因为低估只会让老股被保留，不会被误删。
    """
    out = bars.copy()
    out["listed_days"] = out.groupby("code", sort=False).cumcount() + 1
    return out


def assign_groups(panel: pd.DataFrame, n_groups: int = 5) -> pd.DataFrame:
    """按交易日在截面内分 N 组，组号 1..N，1 为因子值最小。

    panel 需含 trade_date / factor_value。组内股票数不足 5*N 的日期直接剔除，
    避免某组只剩两三只导致均值没有意义。
    """
    out = panel.dropna(subset=["factor_value"]).copy()
    sizes = out.groupby("trade_date")["factor_value"].transform("size")
    out = out[sizes >= n_groups * 5]

    out["pct_rank"] = out.groupby("trade_date")["factor_value"].rank(
        pct=True, method="first"
    )
    out["group"] = (
        np.ceil(out["pct_rank"] * n_groups).clip(1, n_groups).astype("int64")
    )
    return out


def rebalance_dates(dates, horizon: int) -> set:
    """从全部交易日中每 horizon 天取一个调仓日。

    必须用不重叠调仓：如果每天都算一个 20 日持有收益，相邻样本共享 19 天，
    自相关极强，年化和夏普都会被严重高估。
    """
    unique = sorted(set(dates))
    return set(unique[::horizon])


def daily_group_returns(panel: pd.DataFrame) -> pd.DataFrame:
    """每个调仓日各组的等权平均收益，返回 日期 × 组 的宽表。"""
    return (
        panel.dropna(subset=["fwd_return"])
        .groupby(["trade_date", "group"])["fwd_return"]
        .mean()
        .unstack("group")
    )


def summarize(
    daily: pd.DataFrame, n_groups: int = 5, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> pd.DataFrame:
    """把每期收益汇总成年化收益、波动、夏普和胜率。

    periods_per_year 是每年的期数：日频为 252，20 日调仓为 252/20≈12。
    """
    rows = []
    for col in daily.columns:
        series = daily[col].dropna()
        if series.empty:
            continue
        mean = series.mean()
        vol = series.std()
        annual = (1.0 + mean) ** periods_per_year - 1.0
        ann_vol = vol * np.sqrt(periods_per_year)
        rows.append(
            {
                "group": f"G{int(col)}" if isinstance(col, (int, np.integer)) else str(col),
                "periods": len(series),
                "period_mean": mean,
                "annual_return": annual,
                "annual_vol": ann_vol,
                "sharpe": (annual / ann_vol) if ann_vol else np.nan,
                "win_rate": (series > 0).mean(),
            }
        )
    return pd.DataFrame(rows)


def long_short(
    daily: pd.DataFrame, n_groups: int = 5, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> dict:
    """多空组合：做多最高组、做空最低组。"""
    if daily.empty or n_groups not in daily.columns or 1 not in daily.columns:
        return {}
    spread = (daily[n_groups] - daily[1]).dropna()
    if spread.empty:
        return {}
    mean = spread.mean()
    vol = spread.std()
    annual = (1.0 + mean) ** periods_per_year - 1.0
    ann_vol = vol * np.sqrt(periods_per_year)
    return {
        "periods": len(spread),
        "period_mean": mean,
        "annual_return": annual,
        "annual_vol": ann_vol,
        "sharpe": (annual / ann_vol) if ann_vol else np.nan,
        "win_rate": (spread > 0).mean(),
        "cum_return": (1.0 + spread).prod() - 1.0,
    }


def assign_double_groups(
    panel: pd.DataFrame,
    n_groups: int = 5,
    n_layers: int = 3,
    factor_col: str = "factor_value",
    layer_col: str = "layer_value",
) -> pd.DataFrame:
    """双重分组：先按 layer 分 N 层，再在每层内部按 factor 分 M 组。

    用途是控制混淆变量。例如先按波动率分三层，再在各层内部按动量分五组；
    如果各层内部动量都失效，就说明之前看到的 U 型是波动率造成的，不是动量。

    每个交易日独立分层分组，筛选条件：单日股票数需 ≥ n_layers * n_groups * 5。
    """
    out = panel.dropna(subset=[factor_col, layer_col]).copy()
    parts = []
    for _, day in out.groupby("trade_date", sort=False):
        if len(day) < n_layers * n_groups * 5:
            continue
        day = day.copy()
        day["layer"] = (
            np.ceil(day[layer_col].rank(pct=True, method="first") * n_layers)
            .clip(1, n_layers)
            .astype("int64")
        )
        day["group"] = day.groupby("layer")[factor_col].transform(
            lambda s: np.ceil(s.rank(pct=True, method="first") * n_groups)
            .clip(1, n_groups)
            .astype("int64")
        )
        parts.append(day)
    if not parts:
        return out.iloc[0:0].assign(layer=[], group=[])
    return pd.concat(parts, ignore_index=True)


def layer_group_returns(panel: pd.DataFrame) -> pd.DataFrame:
    """每层内各组的平均收益，返回 (layer, group) 两级的宽表。"""
    return (
        panel.dropna(subset=["fwd_return"])
        .groupby(["layer", "trade_date", "group"])["fwd_return"]
        .mean()
        .unstack("group")
    )


def is_monotonic(daily: pd.DataFrame, n_groups: int = 5, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> bool:
    """判断各组年化收益是否严格单调。用于快速判断因子方向是否稳定。"""
    stats = summarize(daily, n_groups, periods_per_year)
    values = stats["annual_return"].tolist()
    if len(values) < 2:
        return False
    return all(b > a for a, b in zip(values, values[1:])) or all(
        b < a for a, b in zip(values, values[1:])
    )
