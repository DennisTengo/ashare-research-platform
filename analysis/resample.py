"""K 线周期聚合。

库里只存日线，周/月/年线在展示时现算。放在 analysis 层而不是页面里，
因为它是纯计算，需要能脱离 Streamlit 单测。

聚合规则（与通行行情软件一致）：
    开盘价取周期内第一个交易日
    收盘价取周期内最后一个交易日
    最高/最低取周期内极值
    成交量、成交额累加
    x 轴刻度用周期内最后一个交易日，读起来就是「这根周线收在哪天」
"""

from __future__ import annotations

import pandas as pd

FREQUENCIES = ("D", "W", "M", "Y")


def to_period(bars: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    """把日线聚合成周/月/年线。

    参数：
        bars  含 trade_date / open / high / low / close / volume / amount，按日期升序
        freq  "D" 原样返回，"W" 周，"M" 月，"Y" 年

    空输入或 freq="D" 时原样返回，不做拷贝。
    """
    if freq == "D" or bars.empty:
        return bars

    if freq not in FREQUENCIES:
        raise ValueError(f"不支持的周期: {freq!r}，可选 {FREQUENCIES}")

    df = bars.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    period = df["trade_date"].dt.to_period(freq)

    return (
        df.groupby(period, sort=True)
        .agg(
            trade_date=("trade_date", "last"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            amount=("amount", "sum"),
        )
        .reset_index(drop=True)
    )
