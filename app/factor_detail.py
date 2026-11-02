"""因子详情页的数据整形。

页面只负责摆布局，数字怎么变成表格、曲线怎么对齐，全在这里。
这些函数都是纯函数（输入是库里的档案字典），不 import streamlit，
所以可以直接单测：不用起界面就能验证「年度表是不是和曲线同一份数据」。

为什么不在这里重算任何东西：
    档案里的数字是离线算好落库的。详情页再去算一遍，就会出现
    「曲线是今天的，绩效表是上周的」这种没人能解释的偏差。
"""

from __future__ import annotations

import pandas as pd

# 三条曲线的固定顺序。顺序固定是为了配色稳定：同一个策略永远同一个颜色，
# 换因子不该换颜色，否则看两张图要重新认一遍。
SERIES_LABELS = ("多空(净)", "多头(净)", "基准(等权)")

_PARAM_LABELS = (
    ("horizon", "持有期（交易日）"),
    ("groups", "分组数"),
    ("min_listed", "最少上市交易日"),
    ("commission", "佣金（双边）"),
    ("stamp_tax", "印花税（卖出）"),
    ("slippage", "滑点（单边）"),
    ("cost_buy", "买入合计成本"),
    ("cost_sell", "卖出合计成本"),
    ("start", "起始日期"),
    ("end", "结束日期"),
    ("universe", "股票池"),
    ("trade_price", "成交价"),
    ("adjust", "价格口径"),
    ("benchmark", "基准"),
)

_PORTFOLIO_COLUMNS = (
    ("label", "组合", None),
    ("periods", "期数", None),
    ("total_return", "累计", "pct"),
    ("annual_return", "年化", "pct"),
    ("annual_vol", "波动", "pct"),
    ("sharpe", "夏普", "num"),
    ("max_drawdown", "最大回撤", "pct"),
    ("calmar", "卡玛", "num"),
    ("win_rate", "胜率", "pct"),
    ("turnover", "换手", "pct"),
)

_YEARLY_COLUMNS = (
    ("year", "年份", None),
    ("periods", "期数", None),
    ("total_return", "收益", "pct"),
    ("annual_return", "年化", "pct"),
    ("annual_vol", "波动", "pct"),
    ("sharpe", "夏普", "num"),
    ("max_drawdown", "最大回撤", "pct"),
    ("win_rate", "胜率", "pct"),
)

_IC_COLUMNS = (
    ("year", "年份", None),
    ("periods", "期数", None),
    ("ic_mean", "IC 均值", "raw"),
    ("ic_std", "IC 标准差", "raw"),
    ("icir", "ICIR", "num"),
    ("win_rate", "IC 胜率", "pct"),
)


def _fmt(value, kind: str | None):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if kind == "pct":
        return round(float(value) * 100, 2)
    if kind == "num":
        return round(float(value), 2)
    if kind == "raw":
        return round(float(value), 4)
    return value


def _table(rows: list[dict], columns) -> pd.DataFrame:
    return pd.DataFrame(
        [{title: _fmt(row.get(key), kind) for key, title, kind in columns} for row in rows]
    )


def settings_table(params: dict | None) -> pd.DataFrame:
    """Simulation Settings：参数 + 实现假设，一起摆出来。

    universe / trade_price / adjust 这几条不是可调参数，是当前实现的假设。
    它们和数字同等重要：对不上券商软件时，答案通常在这里。
    """
    params = params or {}
    return pd.DataFrame(
        [
            {"设置": label, "取值": params.get(key) if params.get(key) is not None else "全部"}
            for key, label in _PARAM_LABELS
        ]
    )


def portfolio_table(record: dict) -> pd.DataFrame:
    rows = (record.get("metrics") or {}).get("portfolios") or []
    return _table(rows, _PORTFOLIO_COLUMNS)


def yearly_table(record: dict, label: str) -> pd.DataFrame:
    rows = (record.get("yearly") or {}).get(label) or []
    return _table(rows, _YEARLY_COLUMNS)


def ic_yearly_table(record: dict) -> pd.DataFrame:
    rows = (record.get("yearly") or {}).get("IC") or []
    return _table(rows, _IC_COLUMNS)


def nav_curve(record: dict) -> tuple[list[str], dict[str, list]]:
    """累计净值曲线：(日期列表, {组合: 净值序列})。

    只取档案里存在的组合，且三条曲线都按同一组日期重采样过，
    所以长度一致、不会出现某条曲线整体错位一期。
    """
    curve = record.get("curve") or {}
    dates = curve.get("dates") or []
    pnl = curve.get("pnl") or {}
    return dates, {label: pnl[label] for label in SERIES_LABELS if label in pnl}


def flat_curve(record: dict, key: str) -> tuple[list[str], list]:
    curve = record.get("curve") or {}
    return curve.get("dates") or [], curve.get(key) or []


def summary(record: dict) -> dict:
    """顶部指标卡要的六个数，全部从绩效表里取，不另算。"""
    portfolios = {
        row.get("label"): row
        for row in (record.get("metrics") or {}).get("portfolios") or []
    }
    groups = (record.get("params") or {}).get("groups") or 0
    long_leg = portfolios.get(f"G{groups}", {})
    spread = portfolios.get("多空(净)", {})
    ic = (record.get("metrics") or {}).get("ic") or {}
    return {
        "多空年化": spread.get("annual_return"),
        "多空夏普": spread.get("sharpe"),
        "多头年化": long_leg.get("annual_return"),
        "多头回撤": long_leg.get("max_drawdown"),
        "IC 均值": ic.get("ic_mean"),
        "IC 胜率": ic.get("win_rate"),
    }


def group_table(record: dict) -> pd.DataFrame:
    """分层收益：只留 G1..Gn，多空与基准归到绩效明细里看。"""
    rows = (record.get("metrics") or {}).get("portfolios") or []
    groups = [row for row in rows if str(row.get("label", "")).startswith("G")]
    return _table(groups, _PORTFOLIO_COLUMNS)


DOUBLE_CLICK = "cellDoubleClicked"


def clicked_factor(event: dict | None, table: pd.DataFrame) -> str | None:
    """从表格组件回传的事件里取出被双击的因子名。

    为什么不在 onClick 上做：单击选行很容易误触，尤其是想复制一个数字的时候。
    双击是「我真的要看它」的明确意图。

    事件里的行数据不一定完整（取决于组件版本），所以这条函数按
    行数据 → 行号 → 已选行 三级回退；取不到就返回 None，让页面停在列表。
    """
    if not event or event.get("streamlitRerunEventTriggerName") != DOUBLE_CLICK:
        return None

    row = event.get("data") or {}
    if isinstance(row, dict) and row.get("因子"):
        return str(row["因子"])

    index = event.get("rowIndex")
    if isinstance(index, int) and 0 <= index < len(table):
        return str(table.iloc[index]["因子"])

    return None