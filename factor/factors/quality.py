"""质量类财务因子。

与价格因子的本质区别：价格因子从行情序列推市场情绪，财务因子从财报看经营本身。
两者相关性低，组合起来比分单用一类更分散。

财务数据的对齐、口径转换（累计转年化、同比计算）全部由引擎在调用前完成，
这里的函数只负责取值和极值处理——保持「因子只管算数」这条边界。

两个数据性质必须记住，否则会造出错误的因子：
  1. 净资产接近零时 ROE 会被放大到 ±600%（如海航 2022H1 的 -601%），
     不截尾会把分组排序完全打乱；
  2. 金融股的营收、毛利率口径与制造业不同，横截面混排等于用两种刻度尺量东西。
     当前未做行业隔离，净利率类因子暂缓上。
"""

from __future__ import annotations

import pandas as pd

from factor.base import Factor, register

ROE_CLIP = (-1.0, 1.0)
YOY_CLIP = (-5.0, 5.0)


def compute_roe(bars: pd.DataFrame) -> pd.Series:
    """年化 ROE（已由数据层按季度数转换）。"""
    return bars["roe_annualized"].clip(*ROE_CLIP)


def compute_net_profit_yoy(bars: pd.DataFrame) -> pd.Series:
    """净利润同比增速。分母为负的期次在数据层已置空。"""
    return bars["net_profit_yoy"].clip(*YOY_CLIP)


register(
    Factor(
        name="roe",
        description="净资产收益率（按季度年化，截尾至 ±100%）",
        lookback=1,
        fn=compute_roe,
        requires=("roe_annualized",),
    )
)

register(
    Factor(
        name="net_profit_yoy",
        description="净利润同比增速（截尾至 ±500%）",
        lookback=1,
        fn=compute_net_profit_yoy,
        requires=("net_profit_yoy",),
    )
)
