"""A 股投研平台 · 首页数据概览。

对应需求 FR-2.1：股票总数、数据区间、最新日期、覆盖率、上次更新时间，
数据明显过期时给出醒目提示。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import plotly.graph_objects as go
import streamlit as st

from app.shared import coverage_by_month, overview

st.set_page_config(page_title="A 股投研平台", page_icon="📈", layout="wide")


def _freshness_banner(last_day, latest_calendar) -> None:
    """数据过期提示。阈值取 3 天：跨一个周末仍属正常。"""
    if last_day is None:
        st.error("行情表为空，先运行 sync_prices 采集数据")
        return
    lag = (dt.date.today() - last_day).days
    if latest_calendar and last_day < latest_calendar:
        missing = (latest_calendar - last_day).days
        st.warning(
            f"数据落后于交易日历 {missing} 天（库内最新 {last_day}，"
            f"日历最新交易日 {latest_calendar}），建议执行增量更新。"
        )
    elif lag > 3:
        st.warning(f"数据最后更新于 {last_day}，距今 {lag} 天，可能已过期。")


def main() -> None:
    st.title("A 股投研平台")
    st.caption("数据 + 因子 + 回测｜本地运行，只读展示")

    data = overview()
    _freshness_banner(data["last_day"], data["latest_calendar"])

    st.subheader("数据规模")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("股票总数", f"{data['stocks']:,}", help="含退市保留的标的")
    c2.metric("行情行数", f"{data['price_rows']:,}")
    c3.metric("财务记录", f"{data['financial_rows']:,}")
    c4.metric("交易日", f"{data['calendar_days']:,}")
    c5.metric("因子个数", len(data["factors"]))

    st.subheader("覆盖区间")
    c1, c2, c3 = st.columns(3)
    c1.metric("行情起始", str(data["first_day"] or "—"))
    c2.metric("行情最新", str(data["last_day"] or "—"))
    c3.metric("财报最新公告", str(data["financial_last_announce"] or "—"))

    st.divider()

    left, right = st.columns([2, 1])
    with left:
        st.subheader("月度入库行数")
        df = coverage_by_month()
        if df.empty:
            st.info("暂无行情数据")
        else:
            fig = go.Figure(
                go.Bar(x=df["month"], y=df["rows"], name="行数", marker_color="#4C78A8")
            )
            fig.update_layout(
                height=320,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis_title=None,
                yaxis_title="行数",
            )
            st.plotly_chart(fig, use_container_width=True)

    with right:
        st.subheader("因子清单")
        rows = [
            {"因子": name, "行数": f"{n:,}"} for name, n in data["factors"]
        ]
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("还没有计算过因子")

        st.subheader("最近任务")
        job = data["last_job"]
        if job:
            st.write(f"**{job[0]}**")
            st.caption(f"{job[1]} ｜ {job[2]}")
        else:
            st.caption("暂无任务记录")


main()
