"""策略回测页。

对应需求 FR-6.1（策略定义）、FR-6.4（成本模型）、FR-6.5（绩效指标）。

和「因子回测」页的区别：
    因子页问「这个因子有没有区分度」——五分组、多空、IC，零成本。
    本页问「按这个信号实际交易能不能赚钱」——单一组合、逐笔成本、
    和等权基准比，成本能追到每一笔成交。

口径（与因子页保持一致，两页期数可直接对照）：
    前 N% 分位等权持有，按因子值排名卡分位阈值，所以每期持股数浮动；
    每 horizon 个交易日调仓一次，非重叠。

尚未实现（页脚会写明，不要把它当成可执行策略）：
    FR-6.2 T+1 开盘成交 —— 现在用的是收盘到收盘
    FR-6.3 停牌与涨跌停不可成交
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import duckdb
import pandas as pd
import streamlit as st

from app.charts import line_option
from app.shared import factor_names
from common.config import load_config

HORIZONS = [5, 10, 20, 60]
QUANTILES = {10: 0.1, 20: 0.2, 30: 0.3}


@st.cache_data(ttl=600, show_spinner=False)
def run_strategy_backtest(
    factor: str,
    horizon: int,
    quantile: float,
    descending: bool,
    start: str | None,
    cost: tuple[float, float, float],
):
    """跑一次策略回测。

    缓存键带上成本三项，改了费率必须重算——否则界面上费率变了数字没变。
    单独建连接：read_only 连接可以并存，缓存命中时不必碰数据库。
    """
    from analysis.costs import CostModel
    from analysis.pipeline import build_panel
    from analysis.strategy import TopQuantile, run_strategy

    conn = duckdb.connect(str(load_config().database_path), read_only=True)
    try:
        panel = build_panel(
            conn, factor, horizon=horizon, groups=None, start=start, end=None
        )
    finally:
        conn.close()

    model = CostModel(commission=cost[0], stamp_tax=cost[1], slippage=cost[2])
    return run_strategy(
        panel,
        TopQuantile(quantile=quantile, descending=descending),
        model,
        label=f"{factor} 前{quantile:.0%}",
        horizon=horizon,
    )


def _nav_chart(result) -> None:
    """净值对比：策略净 / 策略毛 / 等权基准。

    毛和净必须同时画。两条曲线之间的缺口就是成本拖累，
    只画净线看不出「扣费扣了多少」。
    """
    base = result.nav["策略(净)"]
    x = [d.strftime("%Y-%m-%d") for d in base.index]
    series = {name: s.reindex(base.index).tolist() for name, s in result.nav.items()}
    st.echarts_chart(line_option(x, series, height="380px", ylabel="净值"), height=380)


def _drawdown_chart(result) -> None:
    from analysis.backtest import drawdown_series

    base = result.nav["策略(净)"]
    x = [d.strftime("%Y-%m-%d") for d in base.index]
    series = {
        "策略(净)": (drawdown_series(base) * 100).round(2).tolist(),
        "基准(等权)": (drawdown_series(result.nav["基准(等权)"]) * 100).round(2).tolist(),
    }
    st.echarts_chart(line_option(x, series, height="260px", ylabel="回撤 %"), height=260)


def _trade_detail(result) -> None:
    """逐笔成交明细：任意一期、任何一笔都能查到它为什么被收费。"""
    ledger = result.ledger
    if ledger.empty:
        st.info("没有成交记录")
        return

    dates = sorted(ledger["trade_date"].unique())
    picked = st.selectbox(
        "调仓日", dates, index=len(dates) - 1, format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d")
    )
    view = ledger[ledger["trade_date"] == picked].copy()
    period = result.periods.loc[pd.Timestamp(picked)]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("成交笔数", f"{int(period['n_trades'])}")
    c2.metric("买入额", f"{period['turnover_buy']:.1%}", help="占组合净值的比例")
    c3.metric("卖出额", f"{period['turnover_sell']:.1%}")
    c4.metric("本期成本", f"{period['cost']*1e4:.1f} bp", help="万分之一为一个 bp")

    view["占净值"] = (view["delta"].abs() * 100).round(2)
    view["费用(bp)"] = (view["cost"] * 1e4).round(2)
    view = view.rename(
        columns={"code": "股票", "side": "方向", "w_before": "调前权重", "w_after": "调后权重"}
    )[["股票", "方向", "调前权重", "调后权重", "占净值", "费用(bp)"]]
    st.dataframe(view, use_container_width=True, hide_index=True)


def main() -> None:
    st.title("策略回测")

    # 幸存者偏差是这一页最严重的偏差，且数据层改不了，只能先说清楚。
    # 放在表单上方而不是页脚：页脚没人看，而这个偏差会直接抬高收益。
    st.warning(
        "股票池是**当前**沪深 300 成分股。用它回测 2020 年，等于提前知道哪些股票"
        "至今仍在指数里，收益会系统性偏高。本页数字用于验证框架跑通，不能当策略结论引用。"
    )

    names = factor_names()
    if not names:
        st.error("还没有计算过因子，先运行 compute_factors.py")
        return

    defaults = load_config().backtest

    c1, c2, c3, c4 = st.columns(4)
    factor = c1.selectbox("因子", names)
    horizon = c2.selectbox("调仓间隔（交易日）", HORIZONS, index=2)
    quantile_pct = c3.selectbox("持仓档位", list(QUANTILES), index=1, format_func=lambda q: f"前 {q}%")
    descending = (
        c4.selectbox("做多方向", ["因子值最高", "因子值最低"], index=0) == "因子值最高"
    )

    c5, c6 = st.columns([1, 3])
    start = c5.text_input("起始日期", value="2020-01-01")
    with c6.expander("交易成本参数（默认取 config.yaml，可临时改）", expanded=False):
        e1, e2, e3 = st.columns(3)
        commission = e1.number_input(
            "佣金（双边）", value=float(defaults.commission_rate), step=0.0001, format="%.4f"
        )
        stamp = e2.number_input(
            "印花税（卖出）", value=float(defaults.stamp_tax), step=0.0001, format="%.4f"
        )
        slippage = e3.number_input(
            "滑点（双边）", value=float(defaults.slippage), step=0.0001, format="%.4f"
        )
        st.caption(
            f"买入合计 {commission + slippage:.2%} ｜ 卖出合计 {commission + stamp + slippage:.2%}"
            "（每笔按成交金额比例收取，分批成交按金额分摊）"
        )

    if not st.button("运行回测", type="primary"):
        st.info(
            "选好参数后点击运行。点击后需要几秒；同一组参数第二次命中缓存。"
            "口径与「因子回测」页一致：非重叠调仓，可直接对照期数。"
        )
        return

    with st.spinner("计算中…"):
        result = run_strategy_backtest(
            factor, horizon, QUANTILES[quantile_pct], descending,
            start or None, (commission, stamp, slippage),
        )

    if result.n_periods == 0:
        st.warning("样本为空，检查该因子在区间内是否有数据，或降低持仓档位")
        return

    net = result.metrics["策略(净)"]
    gross = result.metrics["策略(毛)"]
    excess = result.metrics["超额(净−基准)"]

    st.caption(
        f"{result.label} ｜ {result.n_periods} 期 ｜ 每 {horizon} 个交易日调仓 ｜ "
        f"每期持股 {int(result.periods['n_holdings'].min())}~{int(result.periods['n_holdings'].max())} 只"
        f"（分位卡阈值，只数浮动）｜ 起始 {start or '全部'}"
    )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("年化（净）", f"{net['annual_return']:+.2%}", help="已扣交易成本")
    m2.metric("年化（毛）", f"{gross['annual_return']:+.2%}", help="未扣成本，两者之差即成本拖累")
    m3.metric("年化超额", f"{excess['annual_return']:+.2%}", help="相对等权基准；基准不扣成本")
    m4.metric("最大回撤", f"{net['max_drawdown']:.2%}")
    m5.metric("每期换手", f"{net['turnover']:.1%}", help="单边成交额占净值比例，首期建仓为 100%")

    _nav_chart(result)
    _drawdown_chart(result)

    from analysis.strategy import metrics_frame

    st.dataframe(metrics_frame(result), use_container_width=True, hide_index=True)

    tabs = st.tabs(["逐笔成交", "每期成本"])
    with tabs[0]:
        _trade_detail(result)
    with tabs[1]:
        show = result.periods.reset_index()
        show["trade_date"] = pd.to_datetime(show["trade_date"]).dt.strftime("%Y-%m-%d")
        show = show.assign(
            **{
                "毛收益(%)": (show["gross"] * 100).round(2),
                "成本(bp)": (show["cost"] * 1e4).round(2),
                "净收益(%)": (show["net"] * 100).round(2),
                "换手(%)": (show["turnover"] * 100).round(1),
            }
        )[["trade_date", "n_holdings", "毛收益(%)", "成本(bp)", "净收益(%)", "换手(%)", "cost_status"]]
        show = show.rename(
            columns={
                "trade_date": "调仓日", "n_holdings": "持股数", "cost_status": "成本核算",
            }
        )
        st.dataframe(show, use_container_width=True, hide_index=True)

    if result.n_pending:
        st.warning(
            f"{result.n_pending} 期的成本标为「待核算」：有持仓股在该期缺收益数据，"
            "期末漂移权重无法精确还原，这些期的成本是近似值，不是 0。"
        )

    st.caption(
        "成本逐笔计费：买入 = 佣金 + 滑点，卖出 = 佣金 + 印花税 + 滑点；"
        "等权组合每期拉回等权产生的再平衡成交同样计费。"
        " ｜ 尚未模拟 T+1 开盘成交（FR-6.2）与停牌、涨跌停不可成交（FR-6.3），"
        "当作可执行策略会高估收益。"
    )


main()