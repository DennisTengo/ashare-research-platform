"""策略层：把因子值变成目标权重，再交给成本模型算账。

对应需求：
    FR-6.1 策略定义接口 —— 策略是「给定当日截面，返回目标权重」的对象
    FR-6.4 成本模型     —— 交给 analysis.costs，逐笔计费
    FR-6.5 绩效指标     —— 毛/净/基准/超额四行对比

与 `analysis.backtest` 的分工：
    backtest.py    回答「因子有没有区分度」——五分组、多空、零假设。
    strategy.py    回答「按这个信号实际交易能不能赚钱」——单一策略、
                   调仓日成交、逐笔成本、与基准比。
    两者共用 `performance` / `nav_series` / `drawdown_series`，不重复实现。

调仓口径（与因子页一致）：
    面板已由 pipeline.build_panel 过滤成非重叠调仓日（每 horizon 个交易日
    取一天），本模块不再自行抽日期——否则两页的期数会对不上。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from analysis.backtest import drawdown_series, nav_series, performance
from analysis.costs import CostModel, drift_weights, rebalance_cost
from common.constants import TRADING_DAYS_PER_YEAR

# 成本状态。缺数据时标「待核算」而不是按 0 计——0 会让结论看起来更好。
STATUS_OK = "完整"
STATUS_PENDING = "待核算"


@dataclass(frozen=True)
class TopQuantile:
    """前 q 分位等权持有。

    选股按截面排名卡分位阈值，所以每期持股数会浮动：300 只池子、q=0.2
    大约每期 58 只，股票池大小一变，持股数跟着变。这是「按分位」而不是
    「固定 30 只」的直接后果，页面上要标出来。

    descending=True 表示做多因子值最高的一档。因子方向目前没写进元信息，
    所以由调用方显式指定，而不是从因子名去猜。
    """

    quantile: float = 0.2
    descending: bool = True
    min_holdings: int = 5
    factor_col: str = "factor_value"

    @property
    def label(self) -> str:
        return f"前{self.quantile:.0%}等权（因子值{'最高' if self.descending else '最低'}）"

    def targets(self, day: pd.DataFrame) -> pd.Series:
        """当日目标权重，index 为代码、合计为 1。

        候选不足 min_holdings 时返回空 Series：两三只股票的等权组合
        没有统计意义，宁可空一期并在结果里计数。
        """
        if day.empty or self.factor_col not in day.columns:
            return pd.Series(dtype=float)

        view = day.dropna(subset=[self.factor_col])
        n = len(view)
        k = int(round(n * self.quantile))
        if k < self.min_holdings:
            return pd.Series(dtype=float)

        # 排序键带上 code，保证因子值并列时选谁不随数据顺序漂移（可复现）
        ranked = view.sort_values(
            [self.factor_col, "code"], ascending=[not self.descending, True]
        )
        codes = ranked["code"].head(k)
        return pd.Series(1.0 / k, index=pd.Index(codes, name="code"))


@dataclass
class StrategyResult:
    """一次策略回测的全部产物。

    periods 每行是一期，ledger 每行是一笔成交，metrics 是四行绩效对比。
    界面只做展示，所有数字来自这里，和脚本、测试共用同一份计算。
    """

    label: str
    params: dict
    periods: pd.DataFrame
    ledger: pd.DataFrame
    nav: dict[str, pd.Series] = field(default_factory=dict)
    metrics: dict[str, dict] = field(default_factory=dict)
    n_skipped: int = 0
    n_pending: int = 0

    @property
    def n_periods(self) -> int:
        return int(len(self.periods))

    def to_dict(self) -> dict:
        """JSON 可序列化，供存档或接口返回。"""
        return {
            "label": self.label,
            "params": self.params,
            "n_periods": self.n_periods,
            "n_skipped": self.n_skipped,
            "n_pending": self.n_pending,
            "metrics": self.metrics,
            "periods": self.periods.reset_index().to_dict(orient="records"),
        }


def _benchmark_returns(panel: pd.DataFrame, dates: list) -> pd.Series:
    """基准：当日全部股票的等权收益，不扣成本（同因子页口径）。

    这是「什么都别做，把池子全买了」的对照组。注意它不是指数：
    库里只有 300 只成分股，没有沪深300 指数行情。
    """
    bench = (
        panel.dropna(subset=["fwd_return"])
        .groupby("trade_date")["fwd_return"]
        .mean()
        .reindex(dates)
    )
    return bench


def run_strategy(
    panel: pd.DataFrame,
    strategy: TopQuantile | None = None,
    cost_model: CostModel | None = None,
    label: str = "",
    horizon: int | None = None,
) -> StrategyResult:
    """按调仓日逐期模拟：选股 → 调仓计费 → 记收益 → 漂移权重。

    panel 需含 code / trade_date / fwd_return / factor_value，
    且已过滤成非重叠调仓日（见 pipeline.build_panel）。
    fwd_return 的时点约定沿用分组的定义：T 日收盘出信号，
    T+1 收盘买入，持有 H 个交易日——**不是** T+1 开盘成交，
    这一条属于 FR-6.2，尚未实现，页面上要标出来。
    """
    strategy = strategy or TopQuantile()
    model = cost_model or CostModel()
    empty = pd.DataFrame()
    if panel.empty:
        return StrategyResult(label=label or strategy.label, params={}, periods=empty, ledger=empty)

    panel = panel.copy()
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])

    rows: list[dict] = []
    trades: list[pd.DataFrame] = []
    prev_end = pd.Series(dtype=float)  # 上期末的漂移权重
    prev_pending = False
    skipped = 0

    for date, day in panel.groupby("trade_date", sort=True):
        target = strategy.targets(day)
        if target.empty:
            # 候选不足，本期不动。漂移权重原样留到下一期，并在下一期标「待核算」
            skipped += 1
            prev_pending = True
            continue

        cost = rebalance_cost(prev_end, target, model)
        if not cost["trades"].empty:
            ledger = cost["trades"].copy()
            ledger.insert(0, "trade_date", date)
            trades.append(ledger)

        rets = day.set_index("code")["fwd_return"].reindex(target.index)
        missing = rets.isna()
        gross = float((target * rets.fillna(0.0)).sum())
        pending = bool(missing.any()) or prev_pending

        rows.append(
            {
                "trade_date": date,
                "n_holdings": len(target),
                "gross": gross,
                "cost": cost["cost"],
                "cost_buy": cost["cost_buy"],
                "cost_sell": cost["cost_sell"],
                "net": gross - cost["cost"],
                "turnover": cost["turnover"],
                "turnover_buy": cost["buy_turnover"],
                "turnover_sell": cost["sell_turnover"],
                "n_trades": cost["n_trades"],
                "cost_status": STATUS_PENDING if pending else STATUS_OK,
            }
        )

        # 期末权重：本期涨跌之后的自然权重，下一期要拉回等权
        prev_end = drift_weights(target, rets)
        prev_pending = pending

    if not rows:
        return StrategyResult(label=label or strategy.label, params={}, periods=empty, ledger=empty)

    periods = pd.DataFrame(rows).set_index("trade_date")
    ledger = pd.concat(trades, ignore_index=True) if trades else empty

    dates = list(periods.index)
    bench = _benchmark_returns(panel, dates)
    periods["bench"] = bench
    # 最后一期若已越过数据末端，未来收益全空：这不是「收益为 0」，而是
    # 这一期还没走完。保留它会让基准与策略的期数对不上（一个 80 一个 79），
    # 所以直接剔掉未完成的那一期。
    periods = periods.dropna(subset=["bench"])
    if periods.empty:
        return StrategyResult(label=label or strategy.label, params={}, periods=empty, ledger=empty)
    periods["excess"] = periods["net"] - periods["bench"]

    dates = list(periods.index)
    ppy = TRADING_DAYS_PER_YEAR / (horizon or _infer_horizon(dates))
    metrics = _summarize(periods, ppy)
    nav = {
        "策略(净)": nav_series(periods["net"]),
        "策略(毛)": nav_series(periods["gross"]),
        "基准(等权)": nav_series(periods["bench"]),
    }
    # 未完成的最后一期已从 periods 剔除，它的成交也不应留在台账里
    if not ledger.empty:
        ledger = ledger[ledger["trade_date"].isin(periods.index)].reset_index(drop=True)

    return StrategyResult(
        label=label or strategy.label,
        params={
            "quantile": strategy.quantile,
            "descending": strategy.descending,
            "min_holdings": strategy.min_holdings,
            "cost": model.as_dict(),
            "horizon": horizon,
            "periods_per_year": round(ppy, 3),
        },
        periods=periods,
        ledger=ledger,
        nav=nav,
        metrics=metrics,
        n_skipped=skipped,
        n_pending=int((periods["cost_status"] == STATUS_PENDING).sum()),
    )


def _infer_horizon(dates: list) -> int:
    """从调仓日间隔反推持有期，用于年化。

    面板已经是非重叠调仓日，相邻两期的交易日间隔就等于 horizon。
    最后一期没有下一期，用前面的中位数补，避免单点异常影响换算。
    """
    if len(dates) < 2:
        return 20
    gaps = pd.Series(dates).diff().dropna().dt.days
    # 自然日间隔 × 5/7 近似交易日数；取中位数抗节假日
    return max(1, int(round(float(gaps.median()) * 5 / 7)))


def _summarize(periods: pd.DataFrame, ppy: float) -> dict[str, dict]:
    """四行绩效对比：策略净、策略毛、基准、超额。

    「扣费前后对比」是 FR-6.5 的硬要求，所以毛和净必须是两行，
    不能只给一个净值——只给净值就看不见成本拖了多少。
    """
    net = performance(periods["net"], ppy, label="策略(净)")
    gross = performance(periods["gross"], ppy, label="策略(毛)")
    bench = performance(periods["bench"], ppy, label="基准(等权)")

    turnover = float(periods["turnover"].mean())
    for row in (net, gross):
        row["turnover"] = turnover
        row["turnover_annual"] = turnover * ppy
    bench["turnover"] = 0.0
    bench["turnover_annual"] = 0.0

    # 超额按「策略净收益 − 基准收益」逐期相减再复利。
    # 波动因此变成跟踪误差，夏普因此变成信息比率。
    excess = performance(periods["excess"], ppy, label="超额(净−基准)")
    excess["turnover"] = turnover

    return {
        "策略(净)": net,
        "策略(毛)": gross,
        "基准(等权)": bench,
        "超额(净−基准)": excess,
    }


def metrics_frame(result: StrategyResult) -> pd.DataFrame:
    """四行绩效表，数值已换算成百分比，供界面直接显示。"""
    rows = []
    for name, m in result.metrics.items():
        rows.append(
            {
                "组合": name,
                "期数": m.get("periods", 0),
                "累计(%)": _pct(m.get("total_return")),
                "年化(%)": _pct(m.get("annual_return")),
                "波动(%)": _pct(m.get("annual_vol")),
                "夏普": _num(m.get("sharpe")),
                "回撤(%)": _pct(m.get("max_drawdown")),
                "卡玛": _num(m.get("calmar")),
                "胜率(%)": _pct(m.get("win_rate")),
                "每期换手(%)": _pct(m.get("turnover")),
            }
        )
    return pd.DataFrame(rows)


def _pct(v) -> float | None:
    return None if v is None or pd.isna(v) else round(float(v) * 100, 2)


def _num(v) -> float | None:
    return None if v is None or pd.isna(v) else round(float(v), 2)