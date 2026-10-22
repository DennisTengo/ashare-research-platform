"""数据校验页。

对应需求 FR-2.2：列出校验问题明细，可按规则类型筛选。

规则实现全部在 quality/checks.py，这里只负责展示——不在界面里重写判断逻辑。
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

from common.config import load_config

st.set_page_config(page_title="数据校验", page_icon="🔎", layout="wide")


@st.cache_data(ttl=300, show_spinner=False)
def run_checks():
    """跑全部校验规则，返回 {规则: [Finding]}。"""
    from quality.checks import run_all

    conn = duckdb.connect(str(load_config().database_path), read_only=True)
    try:
        results = run_all(conn)
    finally:
        conn.close()
    return results


def main() -> None:
    st.title("数据校验")
    st.caption(
        "六条规则：涨跌幅超限、价格成交量异常、重复行、交易日断档、复权因子非单调、零成交却有价格变动。"
    )

    if not st.button("重新校验", type="primary"):
        st.info("点击开始校验。全量数据约需十几秒。")
        return

    with st.spinner("校验中…"):
        results = run_checks()

    from quality.report import summarize

    summary = summarize(results)

    total = sum(item["count"] for item in summary)
    fatal = sum(item["count"] for item in summary if item["fatal"])

    c1, c2, c3 = st.columns(3)
    c1.metric("问题总数", total)
    c2.metric("其中错误", fatal, help="错误级问题会影响分析结论")
    c3.metric("涉及规则", f"{sum(1 for i in summary if i['count'])}/{len(summary)}")

    if total == 0:
        st.success("所有规则通过")
        return

    fig = go.Figure(
        go.Bar(
            x=[item["label"] for item in summary],
            y=[item["count"] for item in summary],
            marker_color=["#E45756" if i["fatal"] else "#F58518" for i in summary],
            text=[i["count"] for i in summary],
            textposition="outside",
        )
    )
    fig.update_layout(
        height=340, yaxis_title="问题数", margin=dict(l=10, r=10, t=20, b=10)
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("明细")
    for item in summary:
        if not item["count"]:
            continue
        with st.expander(f"{item['label']}（{item['rule']}）— {item['count']} 条", expanded=item["fatal"]):
            findings = results.get(item["rule"], [])
            rows = [
                {"代码": f.code or "—", "日期": str(f.trade_date or "—"), "说明": f.detail}
                for f in findings
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True, height=260)
            if item["count"] >= 500:
                st.caption("单规则最多返回 500 条，实际可能更多。")


main()
