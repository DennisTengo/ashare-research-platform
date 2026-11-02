"""因子框架的核心约定。

设计原则只有一条：**因子只管算数，其余全部由引擎负责。**

因子不需要知道数据从哪来、有多少只股票、停牌怎么处理、结果往哪写。
它只接受一段按日期升序的日线，返回一列等长的因子值。

这条边界是整套框架能扩展的前提——加一个新因子只需写一个函数并注册，
不改引擎、不改表结构、不改测试。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
    horizon:     评估这个因子时的持有交易日数，同时决定调仓间隔。
    groups:      评估这个因子时的分组数，最高组为多头、最低组为空头。

    为什么 horizon / groups 归因子所有，而不是做成全局参数：
        一个因子的经济含义自带时间尺度——动量看 20 日、价值看季度。
        让使用者每次在界面上挑一组参数，等于允许用 12 种参数反复试到好看为止，
        结果不可追溯。写在因子里，参数就是因子定义的一部分：
        参数变了就是另一个档案（新的 params_hash），旧的数字仍然说得清。
    """

    name: str
    description: str
    lookback: int
    fn: Callable[[pd.DataFrame], pd.Series]
    requires: tuple[str, ...] = ()
    horizon: int = 20
    groups: int = 5

    @property
    def settings(self) -> dict:
        """评估设置。写进报告的 Simulation Settings，也是 params_hash 的输入。"""
        return {"horizon": self.horizon, "groups": self.groups}


_REGISTRY: dict[str, Factor] = {}


def register(factor: Factor) -> Factor:
    """注册因子。重名直接报错——静默覆盖会让结果无从追溯。"""
    if factor.name in _REGISTRY:
        raise ValueError(f"因子重名: {factor.name}")
    if factor.lookback < 1:
        raise ValueError(f"因子 {factor.name} 的 lookback 必须为正")
    if not 1 <= factor.horizon <= 252:
        raise ValueError(
            f"因子 {factor.name} 的 horizon 必须在 1~252 个交易日内，收到 {factor.horizon}"
        )
    if not 2 <= factor.groups <= 20:
        raise ValueError(
            f"因子 {factor.name} 的 groups 必须在 2~20 之间，收到 {factor.groups}"
        )
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
