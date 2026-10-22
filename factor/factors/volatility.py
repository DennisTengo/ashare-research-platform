"""20 日波动率因子。

含义：过去 20 个交易日日收益率的样本标准差。
经济逻辑：低波动异象——低波动股票的风险调整后收益往往更高。
与动量因子的关键区别：它和涨跌方向无关，是一个独立的维度，
适合用来检验框架能否处理"非收益率类"的因子。
"""

from __future__ import annotations

import pandas as pd

from factor.base import Factor, register

WINDOW = 20


def compute(bars: pd.DataFrame) -> pd.Series:
    """过去 N 日收益率的标准差。日收益率由复权价计算。"""
    returns = bars["adj_close"].pct_change()
    return returns.rolling(WINDOW).std()


register(
    Factor(
        name="volatility_20d",
        description="过去 20 个交易日日收益率标准差",
        lookback=WINDOW + 1,
        fn=compute,
    )
)
