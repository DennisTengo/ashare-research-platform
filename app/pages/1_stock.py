"""个股查询页。

对应需求 FR-3.1（K 线与成交量、区间与复权切换）、FR-3.3（财务指标表）、
FR-3.4（基本信息卡）。

复权换算在界面层完成：库里只有原始价和因子，不存前复权价。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from app.shared import apply_adjust, financials, price_bars, stock_options

st.set_page_config(page_title="个股查询", page_icon="🔍", layout="wide")

RANGES = {"1 个月": 30, "6 个月": 182, "1 年": 365, "3 年": 1095, "全部": None}
ADJUST = {"不复权": "raw", "前复权": "forward", "后复权": "back"}


def _picker(stocks) -> str:
    """代码搜索框 + 下拉。搜索比滚 5500 行更快。"""
    query = st.text_input("搜索代码或名称", placeholder="例如 600519 或 茅台")
    view = stocks
    if query:
        mask = stocks["code"].str.contains(query, na=False) | stocks["name"].str.contains(
            query, na=False
        )
        view = stocks[mask]
    if view.empty:
        st.warning("没有匹配的股票")
        return ""
    labels = [f"{r.code} {r.name}" for r in view.itertuples()]
    picked = st.selectbox("选择股票", labels, index=0)
    return picked.split(" ")[0]


def _chart(df, title: str) -> None:
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.03
    )
    fig.add_trace(
        go.Candlestick(
            x=df["trade_date"], open=df["open"], high=df["high"],
            low=df["low"], close=df["close"], name="K 线",
        ),
        row=1, col=1,
    )
    colors = ["#E45756" if c >= o else "#54A24B" for c, o in zip(df["close"], df["open"])]
    fig.add_trace(
        go.Bar(x=df["trade_date"], y=df["volume"], name="成交量", marker_color=colors),
        row=2, col=1,
    )
    fig.update_layout(
        height=560,
        title=title,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=40, b=10),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def _info_card(row) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("代码", row.code)
    c2.metric("名称", row.name)
    c3.metric("板块", row.board)
    c4.metric("上市日", str(row.list_date) if row.list_date else "未知")
    c5.metric("状态", "ST" if row.is_st else "正常")


def main() -> None:
    st.title("个股查询")
    stocks = stock_options()
    if stocks.empty:
        st.error("股票表为空")
        return

    code = _picker(stocks)
    if not code:
        return

    row = stocks[stocks["code"] == code].iloc[0]
    _info_card(row)

    st.divider()
    c1, c2, c3 = st.columns([2, 2, 3])
    range_label = c1.selectbox("区间", list(RANGES), index=2)
    adjust_label = c2.selectbox("复权", list(ADJUST), index=1)

    today = dt.date.today()
    start = (
        (today - dt.timedelta(days=RANGES[range_label])).isoformat()
        if RANGES[range_label]
        else "1990-01-01"
    )
    end = today.isoformat()

    bars = price_bars(code, start, end)
    if bars.empty:
        st.info("该区间没有行情数据")
        return

    view = apply_adjust(bars, ADJUST[adjust_label])
    latest = view.iloc[-1]
    prev = view.iloc[-2] if len(view) > 1 else latest
    pct = (latest["close"] / prev["close"] - 1) * 100 if prev["close"] else 0.0
    c3.metric(
        "最新收盘",
        f"{latest['close']:.2f}",
        delta=f"{pct:+.2f}%",
        help=f"区间 {view['trade_date'].iloc[0].date()} ~ {view['trade_date'].iloc[-1].date()}",
    )

    _chart(view, f"{code} {row.name}｜{adjust_label}｜{range_label}")

    if ADJUST[adjust_label] == "forward":
        st.caption("前复权以区间最后一日为基准，最新价格与不复权一致；切换区间时历史价格会随之变化。")

    st.divider()
    st.subheader("财务指标")
    fin = financials(code)
    if fin.empty:
        st.info("该股票暂无财务数据")
    else:
        st.caption(
            "公告日是该期数据真正可见的日期。用报告期对齐会提前一个月以上知道答案，"
            "这里的每一行都标注了它何时才进入市场。"
        )
        show = fin.copy()
        show["roe"] = (show["roe"] * 100).round(2)
        show["gross_margin"] = (show["gross_margin"] * 100).round(2)
        show["net_margin"] = (show["net_margin"] * 100).round(2)
        show = show.rename(
            columns={
                "report_period": "报告期", "announce_date": "公告日", "revenue": "营收",
                "net_profit": "净利润", "roe": "ROE(%)", "eps": "EPS",
                "gross_margin": "毛利率(%)", "net_margin": "净利率(%)",
            }
        ).drop(columns=["extra"])
        st.dataframe(show, use_container_width=True, hide_index=True, height=380)


main()
