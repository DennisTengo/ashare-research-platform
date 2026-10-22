"""IC 分析（Information Coefficient）。

定义：每个调仓日，计算「因子值的截面排序」与「未来收益的截面排序」的秩相关系数。
一期得到一个 IC 值，多期构成一条 IC 序列。

为什么用秩相关（Spearman）而不是线性相关：
    因子值分布往往极度偏斜（例如动量从 -53% 到 +573%），线性相关会被极端值主导。
    秩相关只看排名，对极端值免疫，是因子研究的标准做法。

判断标准（经验值，A 股）：
    |IC 均值| < 0.02   基本无信息
    0.02 ~ 0.05       弱，可能可用
    0.05 ~ 0.10       不错
    > 0.10            很强，先怀疑数据是否有未来函数
    ICIR > 0.3        方向稳定
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common.logger import get_logger

log = get_logger("analysis.ic")


def _rank_corr(x: pd.Series, y: pd.Series) -> float:
    """秩相关系数。

    等价于 Spearman，但不依赖 scipy——pandas 的 method="spearman" 内部会
    调用 scipy.stats.spearmanr，而本项目的依赖里没有 scipy。
    做法就是把两列各自转成排名再算 Pearson，数学上完全等价。
    """
    return x.rank().corr(y.rank())


def compute_ic(
    panel: pd.DataFrame,
    factor_col: str = "factor_value",
    return_col: str = "fwd_return",
    min_stocks: int = 30,
) -> pd.Series:
    """按交易日计算截面秩相关，返回 index 为日期的 Series。

    单日有效股票数不足 min_stocks 时跳过——样本太少算出来的相关系数噪声极大。
    """
    results: dict[pd.Timestamp, float] = {}
    for date, group in panel.groupby("trade_date", sort=True):
        clean = group.dropna(subset=[factor_col, return_col])
        if len(clean) < min_stocks:
            continue
        corr = _rank_corr(clean[factor_col], clean[return_col])
        if pd.notna(corr):
            results[date] = float(corr)
    series = pd.Series(results, dtype=float).sort_index()
    series.index.name = "trade_date"
    return series


def ic_summary(ic: pd.Series, periods_per_year: float | None = None) -> dict:
    """汇总 IC 序列。

    periods_per_year 仅用于把 ICIR 年化；不传则给原始值（每期）。
    """
    clean = ic.dropna()
    n = len(clean)
    if n == 0:
        return {
            "periods": 0,
            "ic_mean": float("nan"),
            "ic_std": float("nan"),
            "icir": float("nan"),
            "win_rate": float("nan"),
            "t_stat": float("nan"),
        }
    mean = clean.mean()
    std = clean.std()
    icir = mean / std if std else float("nan")
    t_stat = mean / (std / np.sqrt(n)) if std else float("nan")
    out = {
        "periods": n,
        "ic_mean": mean,
        "ic_std": std,
        "icir": icir,
        "win_rate": (clean > 0).mean(),
        "t_stat": t_stat,
    }
    if periods_per_year:
        out["icir_annual"] = icir * np.sqrt(periods_per_year)
    return out


def ic_decay(
    bars: pd.DataFrame,
    factor: pd.DataFrame,
    horizons: list[int],
    min_stocks: int = 30,
    min_listed_days: int = 60,
    rebalance_every: int | None = None,
) -> pd.DataFrame:
    """不同持有期下的 IC 均值，用于判断信号衰减速度。

    bars 需含 code / trade_date / adj_close，且已按 (code, trade_date) 排序。
    factor 需含 code / trade_date / factor_value。
    """
    from analysis.grouping import add_forward_return, add_listed_days, rebalance_dates

    rows = []
    for h in horizons:
        frame = add_forward_return(bars, h)
        frame = add_listed_days(frame)
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        panel = frame.merge(factor, on=["code", "trade_date"], how="inner")
        panel = panel[panel["listed_days"] >= min_listed_days]
        if rebalance_every:
            dates = rebalance_dates(panel["trade_date"], rebalance_every)
            panel = panel[panel["trade_date"].isin(dates)]

        ic = compute_ic(panel, min_stocks=min_stocks)
        stats = ic_summary(ic)
        rows.append(
            {
                "horizon": h,
                "periods": stats["periods"],
                "ic_mean": stats["ic_mean"],
                "ic_std": stats["ic_std"],
                "icir": stats["icir"],
                "win_rate": stats["win_rate"],
            }
        )
    return pd.DataFrame(rows)
