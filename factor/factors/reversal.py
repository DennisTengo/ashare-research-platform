"""20 日反转因子。

含义：动量因子取负号。
经济逻辑：短期涨多了会回调（反转效应），在 A 股这类散户占比高的市场更常见。

为什么同时保留动量和反转：两者互为镜像，如果两个因子的分层收益方向相反且对称，
说明框架的排序、分组、收益计算这条链路没有搞反方向——这是一个便宜的自检手段。
"""

from __future__ import annotations

import pandas as pd

from factor.base import Factor, register

WINDOW = 20


def compute(bars: pd.DataFrame) -> pd.Series:
    """过去 N 日收益率的相反数。"""
    return -bars["adj_close"].pct_change(WINDOW)


register(
    Factor(
        name="reversal_20d",
        description="过去 20 个交易日复权收益率的相反数",
        lookback=WINDOW + 1,
        fn=compute,
    )
)
