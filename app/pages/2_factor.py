"""因子分析页。

对应需求 FR-4.1（因子清单与参数）、FR-4.2（分层收益）、
FR-4.3（多空净值）、FR-4.4（IC 分析）。

所有计算复用 analysis.pipeline，与命令行、测试、存档共用同一套口径——
界面不做任何自己的统计，避免出现「界面看到的和脚本跑出来的不一致」。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import duckdb
import plotly.graph_objects as go
import streamlit as st

from app.shared import factor_names, factor_registry
from common.config import load_config

st.set_page_config(page_title="因子分析", page_icon="📊", layout="wide")


@st.cache_data(ttl=600, show_spinner=False)
def run_analysis(factor: str, horizon: int, groups: int, start: str | None, end: str | None):
    """跑一次完整分析。返回结构化结果供页面渲染。

    单独建连接而不是复用 resource 连接：read_only 连接可以并存，
    而且缓存命中时不需要碰数据库。
    """
    from analysis.ic import compute_ic, ic_summary
    from analysis.pipeline import build_panel, run_backtest
    from analysis.grouping import TRADING_DAYS_PER_YEAR

    conn = duckdb.connect(str(load_config().database_path), read_only=True)
    try:
        result = run_backtest(
            conn, factor, horizon=horizon, groups=groups, start=start, end=end
        )
        panel = build_panel(conn, factor, horizon=horizon, groups=None, start=start, end=end)
        ic = compute_ic(panel) if not panel.empty else None
        ppy = max(1, round(TRADING_DAYS_PER_YEAR / horizon))
        stats = ic_summary(ic, ppy) if ic is not None else None
    finally:
        conn.close()
    return result, ic, stats


def _group_chart(result) -> None:
    groups = result.groups
    view = groups[groups["label"].str.startswith("G")]
    if view.empty:
        return
    fig = go.Figure(
        go.Bar(
            x=view["label"],
            y=view["annual_return"] * 100,
            marker_color="#4C78A8",
            text=[f"{v*100:.2f}%" for v in view["annual_return"]],
            textposition="outside",
        )
    )
    fig.update_layout(
        height=340,
        title="各分组年化收益（G1 因子值最低，G5 最高）",
        yaxis_title="年化 %",
        margin=dict(l=10, r=10, t=50, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)


def _nav_chart(result) -> None:
    fig = go.Figure()
    for label, series in result.nav.items():
        fig.add_trace(go.Scatter(x=series.index, y=series.values, name=label, mode="lines"))
    fig.update_layout(
        height=380,
        title="累计净值（多空组合已扣成本）",
        yaxis_title="净值",
        margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", y=-0.15),
    )
    st.plotly_chart(fig, use_container_width=True)


def _ic_chart(ic, stats) -> None:
    if ic is None or ic.empty:
        st.info("IC 序列为空")
        return
    fig = go.Figure(go.Bar(x=ic.index, y=ic.values, marker_color="#E45756"))
    fig.add_hline(y=0, line_dash="dot", line_color="#888")
    fig.add_hline(
        y=stats["ic_mean"], line_dash="dash", line_color="#4C78A8",
        annotation_text=f"均值 {stats['ic_mean']:+.4f}",
    )
    fig.update_layout(
        height=320,
        title="IC 时序（截面秩相关）",
        yaxis_title="IC",
        margin=dict(l=10, r=10, t=50, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)


def main() -> None:
    st.title("因子分析")

    names = factor_names()
    if not names:
        st.error("还没有计算过因子，先运行 compute_factors.py")
        return

    registry = factor_registry()
    with st.expander("因子说明", expanded=False):
        st.dataframe(
            [
                {
                    "因子": n,
                    "说明": registry.get(n, {}).get("description", "（未注册，可能来自旧版本）"),
                    "需要财务字段": ", ".join(registry.get(n, {}).get("requires", ())) or "—",
                }
                for n in names
            ],
            use_container_width=True,
            hide_index=True,
        )

    c1, c2, c3, c4 = st.columns(4)
    factor = c1.selectbox("因子", names)
    horizon = c2.selectbox("持有期（交易日）", [5, 10, 20, 60], index=2)
    groups = c3.selectbox("分组数", [3, 5, 10], index=1)
    start = c4.text_input("起始日期", value="2020-01-01")

    if not st.button("运行分析", type="primary"):
        st.info("选择因子后点击运行。全市场计算需要几秒。")
        return

    with st.spinner("计算中…"):
        result, ic, stats = run_analysis(factor, horizon, groups, start or None, None)

    if result.n_periods == 0:
        st.warning("样本为空，检查该因子在区间内是否有数据")
        return

    st.caption(
        f"样本 {result.n_periods} 期 ｜ 每 {horizon} 个交易日调仓 ｜ "
        f"{groups} 分组 ｜ 起始 {start or '全部'}"
    )

    if stats:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("IC 均值", f"{stats['ic_mean']:+.4f}")
        m2.metric("t 值", f"{stats['t_stat']:+.2f}", help="|t| < 2 时统计上不显著")
        m3.metric("IC 胜率", f"{stats['win_rate']:.1%}")
        ls = result.long_short["net"]
        m4.metric("多空年化（净）", f"{ls['annual_return']:+.2%}")

    _group_chart(result)
    _nav_chart(result)
    _ic_chart(ic, stats)

    with st.expander("绩效明细", expanded=True):
        show = result.groups.copy()
        for col in ("total_return", "annual_return", "annual_vol", "max_drawdown", "win_rate", "turnover"):
            if col in show.columns:
                show[col] = (show[col] * 100).round(2)
        show["sharpe"] = show["sharpe"].round(2)
        if "calmar" in show.columns:
            show["calmar"] = show["calmar"].round(2)
        show = show.rename(
            columns={
                "label": "组合", "periods": "期数", "total_return": "累计(%)",
                "annual_return": "年化(%)", "annual_vol": "波动(%)", "sharpe": "夏普",
                "max_drawdown": "回撤(%)", "calmar": "卡玛", "win_rate": "胜率(%)",
                "turnover": "换手(%)",
            }
        )
        st.dataframe(show, use_container_width=True, hide_index=True)

    st.caption(
        "多空组合在 A 股受融券限制，仅作因子有效性的理论度量。"
        "当前回测未模拟涨跌停与停牌不可成交，当作可执行策略会高估收益。"
    )


main()
