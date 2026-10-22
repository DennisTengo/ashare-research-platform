"""20 日动量因子。

含义：过去 20 个交易日的复权收益率。
经济逻辑：强者恒强（动量效应），涨得多的短期还会继续涨。
"""

from __future__ import annotations

import pandas as pd

from factor.base import Factor, register

WINDOW = 20


def compute(bars: pd.DataFrame) -> pd.Series:
    """过去 N 日收益率。bars 需含 adj_close 且按日期升序。"""
    return bars["adj_close"].pct_change(WINDOW)


register(
    Factor(
        name="momentum_20d",
        description="过去 20 个交易日复权收益率",
        lookback=WINDOW + 1,
        fn=compute,
    )
)
