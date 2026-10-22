"""因子框架的核心约定。

设计原则只有一条：**因子只管算数，其余全部由引擎负责。**

因子不需要知道数据从哪来、有多少只股票、停牌怎么处理、结果往哪写。
它只接受一段按日期升序的日线，返回一列等长的因子值。

这条边界是整套框架能扩展的前提——加一个新因子只需写一个函数并注册，
不改引擎、不改表结构、不改测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass(frozen=True)
class Factor:
    """一个因子的完整描述。

    name:        唯一标识，也是 fact_factor.factor_name 的取值
    description: 人话说明，用于报告和界面
    lookback:    计算所需的最少历史交易日数。不足时该股票输出 NaN
    fn:          计算函数，输入 bars，输出 Series
    requires:    除行情外还需要合并的财务字段。为空表示纯价格因子；
                 非空时引擎会在调用前按公告日把财务数据对齐到日频。
    """

    name: str
    description: str
    lookback: int
    fn: Callable[[pd.DataFrame], pd.Series]
    requires: tuple[str, ...] = ()


_REGISTRY: dict[str, Factor] = {}


def register(factor: Factor) -> Factor:
    """注册因子。重名直接报错——静默覆盖会让结果无从追溯。"""
    if factor.name in _REGISTRY:
        raise ValueError(f"因子重名: {factor.name}")
    if factor.lookback < 1:
        raise ValueError(f"因子 {factor.name} 的 lookback 必须为正")
    if factor.requires:
        from analysis.financial import AVAILABLE_FIELDS

        unknown = [c for c in factor.requires if c not in AVAILABLE_FIELDS]
        if unknown:
            raise ValueError(
                f"因子 {factor.name} 声明了不可用的财务字段 {unknown}，可选：{AVAILABLE_FIELDS}"
            )
    _REGISTRY[factor.name] = factor
    return factor


def get(name: str) -> Factor:
    try:
        return _REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY)) or "（空）"
        raise KeyError(f"未知因子 {name!r}，已注册：{available}") from None


def all_factors() -> dict[str, Factor]:
    """返回已注册因子的副本，避免外部直接改注册表。"""
    return dict(_REGISTRY)
