"""编排层：把一条完整的回测流程封装成可复用的函数。

存在的理由：
    在这之前，「读行情 → 算未来收益 → 合并因子 → 过滤 → 调仓日 → 分组」
    这套准备逻辑在四个脚本里各写了一遍。命令行下复制粘贴没成本，
    但界面、测试、存档都要用同一套逻辑时，重复就会变成口径不一致的源头。

分层约定：
    本模块只做编排，不实现计算。所有数值来自 analysis 下的纯函数。
    CLI、Streamlit、测试、存档四种消费方式共用这里的入口。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb
import pandas as pd

from analysis.backtest import (
    DEFAULT_COST_BUY,
    DEFAULT_COST_SELL,
    drawdown_series,
    long_short_returns,
    nav_series,
    performance,
    portfolio_returns,
)
from analysis.grouping import (
    TRADING_DAYS_PER_YEAR,
    add_forward_return,
    add_listed_days,
    assign_groups,
    rebalance_dates,
)
from analysis.record import record_backtest
from common.logger import get_logger
from factor.engine import load_bars

log = get_logger("analysis.pipeline")


def load_factor(conn: duckdb.DuckDBPyConnection, name: str, as_col: str = "factor_value") -> pd.DataFrame:
    """读取因子值并统一列名，避免各脚本重复写同一段 SQL。"""
    df = conn.execute(
        f"SELECT code, trade_date, value AS {as_col} FROM fact_factor WHERE factor_name = ?",
        [name],
    ).df()
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def build_panel(
    conn: duckdb.DuckDBPyConnection,
    factor: str,
    horizon: int = 20,
    groups: int | None = 5,
    min_listed: int = 60,
    rebalance: bool = True,
    start: str | None = None,
    end: str | None = None,
    extra_factors: dict[str, str] | None = None,
) -> pd.DataFrame:
    """构建回测面板。

    每一行是 (交易日, 股票, 因子值, 分组, 未来收益)。

    参数：
        groups        为 None 时不做分组，返回未分组的原始面板（供双重分组等场景使用）
        rebalance     是否只保留调仓日。不重叠调仓可避免样本自相关
        extra_factors 额外因子，{目标列名: 因子名}，例如 {"layer_value": "volatility_20d"}
    """
    bars = load_bars(conn, start, end)
    if bars.empty:
        log.warning("没有行情数据")
        return pd.DataFrame()

    frame = add_forward_return(bars, horizon)
    frame = add_listed_days(frame)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])

    fac = load_factor(conn, factor)
    if fac.empty:
        log.warning("因子 {} 没有数据", factor)
        return pd.DataFrame()

    panel = frame.merge(fac, on=["code", "trade_date"], how="inner")
    for col, name in (extra_factors or {}).items():
        extra = load_factor(conn, name, as_col=col)
        if extra.empty:
            log.warning("附加因子 {} 没有数据", name)
            return pd.DataFrame()
        panel = panel.merge(extra, on=["code", "trade_date"], how="inner")

    panel = panel[panel["listed_days"] >= min_listed]
    if rebalance:
        dates = rebalance_dates(panel["trade_date"], horizon)
        panel = panel[panel["trade_date"].isin(dates)]
    if groups:
        panel = assign_groups(panel, groups)
    return panel


@dataclass
class BacktestResult:
    """一次组合回测的完整结果。

    同时服务三种消费方式：
        CLI      直接打印各字段
        Streamlit  groups 喂给 st.dataframe，nav 喂给 st.line_chart
        存档      to_dict() 序列化为 JSON
    """

    factor: str
    params: dict
    n_periods: int
    groups: pd.DataFrame                    # 每个组合一行绩效
    long_short: dict = field(default_factory=dict)
    benchmark: dict = field(default_factory=dict)
    nav: dict[str, pd.Series] = field(default_factory=dict)
    drawdown: dict[str, pd.Series] = field(default_factory=dict)

    @property
    def labels(self) -> list[str]:
        return list(self.nav)

    def to_dict(self) -> dict:
        """JSON 可序列化，供存档或接口返回。"""
        return {
            "factor": self.factor,
            "params": self.params,
            "n_periods": self.n_periods,
            "groups": self.groups.to_dict(orient="records"),
            "long_short": self.long_short,
            "benchmark": self.benchmark,
            "nav": {
                label: {
                    "dates": [d.strftime("%Y-%m-%d") for d in series.index],
                    "values": [round(float(v), 6) for v in series.values],
                }
                for label, series in self.nav.items()
            },
        }


def run_backtest(
    conn: duckdb.DuckDBPyConnection,
    factor: str,
    horizon: int = 20,
    groups: int = 5,
    min_listed: int = 60,
    cost_buy: float = DEFAULT_COST_BUY,
    cost_sell: float = DEFAULT_COST_SELL,
    start: str | None = None,
    end: str | None = None,
    save: bool = False,
    label: str = "默认参数",
) -> BacktestResult:
    """跑一次完整回测：分组 → 逐组组合 → 多空 → 基准，返回结构化结果。"""
    panel = build_panel(
        conn, factor, horizon=horizon, groups=groups,
        min_listed=min_listed, start=start, end=end,
    )
    if panel.empty:
        return BacktestResult(factor=factor, params={}, n_periods=0, groups=pd.DataFrame())

    ppy = max(1, round(TRADING_DAYS_PER_YEAR / horizon))
    rows: list[dict] = []
    navs: dict[str, pd.Series] = {}

    def _add(label_: str, result: pd.DataFrame, perf: dict) -> None:
        rows.append({**perf, "turnover": float(result["turnover"].mean())})
        navs[label_] = nav_series(result["net"])

    # 基准：全部股票等权，不扣成本
    bench = portfolio_returns(panel, None, 0.0, 0.0)
    benchmark = performance(bench["net"], ppy, label="基准(等权)")
    _add("基准(等权)", bench, benchmark)

    for g in range(1, groups + 1):
        result = portfolio_returns(panel, g, cost_buy, cost_sell)
        _add(f"G{g}", result, performance(result["net"], ppy, label=f"G{g}"))

    ls = long_short_returns(panel, groups, cost_buy, cost_sell)
    ls_gross = performance(ls["gross"], ppy, label="多空(毛)")
    ls_net = performance(ls["net"], ppy, label="多空(净)")
    rows.append({**ls_gross, "turnover": float(ls["turnover"].mean())})
    rows.append({**ls_net, "turnover": float(ls["turnover"].mean())})
    navs["多空(净)"] = nav_series(ls["net"])

    params = {
        "horizon": horizon,
        "groups": groups,
        "cost_buy": cost_buy,
        "cost_sell": cost_sell,
        "min_listed": min_listed,
        "start": start,
        "end": end,
    }
    result_obj = BacktestResult(
        factor=factor,
        params=params,
        n_periods=int(panel["trade_date"].nunique()),
        groups=pd.DataFrame(rows),
        long_short={"gross": ls_gross, "net": ls_net},
        benchmark=benchmark,
        nav=navs,
        drawdown={label_: drawdown_series(series) for label_, series in navs.items()},
    )

    if save:
        metrics = result_obj.to_dict()
        record_backtest(conn, factor, label, params, metrics["groups"])

    return result_obj
