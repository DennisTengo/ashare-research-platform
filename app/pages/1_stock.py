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
import pandas as pd
import streamlit as st
from plotly.subplots import make_subplots

from app.shared import (
    apply_adjust,
    financials,
    missing_trading_days,
    price_bars,
    rangebreaks,
    stock_options,
)

st.set_page_config(page_title="个股查询", page_icon="🔍", layout="wide")

RANGES = {"1 个月": 30, "6 个月": 182, "1 年": 365, "3 年": 1095, "全部": None}
ADJUST = {"不复权": "raw", "前复权": "forward", "后复权": "back"}


def _picker(stocks) -> str:
    """代码与名称合一的下拉搜索。

    Streamlit 的 selectbox 自带输入过滤，比「先输入框再下拉」两步操作
    更接近搜索引擎的体验，也避免两个控件状态不同步的问题。
    """
    labels = [f"{r.code}  {r.name}" for r in stocks.itertuples()]
    picked = st.selectbox(
        "搜索代码或名称",
        options=labels,
        index=None,
        placeholder="输入代码或名称，例如 600519 或 茅台",
    )
    if not picked:
        return ""
    # 代码里不含空格，用双空格分隔可避开“万  科Ａ”这类名称自带空格的情况
    return picked.split("  ")[0]


def _chart(df, title: str, start: str, end: str) -> None:
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
        # 高度必须显式给定：Plotly 在 iframe 里拿不到容器高度，autosize 会退化成默认值，
        # 全屏后子图比例失调、成交量被挤出视口。
        height=620,
        title=title,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=40, b=10),
        showlegend=False,
        hovermode="x unified",
        dragmode="zoom",
    )
    # 压缩休市日：日期轴默认按真实时间比例铺开，春节长假会留出一大段空白。
    # 只跳休市日，停牌日仍然留空——两者在图上应该区分开。
    fig.update_xaxes(rangebreaks=rangebreaks(start, end))
    # 锁定纵轴：Plotly 框选缩放会同时缩 x 和 y，纵向被框到很窄的价格带后，
    # K 线就被挤出视野、整张图变成空白。fixedrange 让纵向不参与缩放，
    # 框选与滚轮只作用于时间轴；纵向范围由当前数据自动决定。
    fig.update_yaxes(fixedrange=True)
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "displaylogo": False,
            # 滚轮缩放以鼠标位置为锚点，比点击按钮以中心为锚点自然得多
            "scrollZoom": True,
            # 去掉 zoomIn2d / zoomOut2d：它们是“以绘图区中心等比放大”，
            # 连点几次后 K 线右端和成交量会被推出可视区域。
            # 保留框选缩放（zoom2d）和重置（resetScale2d）。
            "modeBarButtonsToRemove": [
                "zoomIn2d", "zoomOut2d", "autoScale2d", "toggleSpikelines",
            ],
        },
    )


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

    _chart(view, f"{code} {row.name}｜{adjust_label}｜{range_label}", start, end)

    # 导出图表用的一控制：停牌日仍留空，不参与休市日压缩
    missing = missing_trading_days(code, start, end)
    if missing:
        st.caption(
            f"该区间内有 {len(missing)} 个交易日无数据（停牌或未上市），图上仍保留空档，未按休市日压缩。"
        )

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

        # 金额统一换成亿元，并给反推值加星号。
        # 不标出来的话，用户会以为这是数据源给的原始营收。
        def _money(series, estimated=None):
            out = []
            for i, v in enumerate(series):
                if pd.isna(v):
                    out.append("—")
                    continue
                text = f"{v / 1e8:.2f}"
                if estimated is not None and bool(estimated.iloc[i]):
                    text += " *"
                out.append(text)
            return out

        est = show["revenue_estimated"] if "revenue_estimated" in show.columns else None
        show["revenue"] = _money(show["revenue"], est)
        show["net_profit"] = _money(show["net_profit"])

        show = show.rename(
            columns={
                "report_period": "报告期", "announce_date": "公告日", "revenue": "营收(亿)",
                "net_profit": "净利润(亿)", "roe": "ROE(%)", "eps": "EPS",
                "gross_margin": "毛利率(%)", "net_margin": "净利率(%)",
            }
        ).drop(columns=["extra", "revenue_estimated"], errors="ignore")
        st.dataframe(show, use_container_width=True, hide_index=True, height=380)
        st.caption(
            "带 * 的营收为按「净利润 ÷ 净利率」反推——数据源不提供一季报和三季报的营收。"
            "全样本验证中位误差 0.0006%，但净利率绝对值小于 0.5% 时不反推（误差会放大）。"
        )


main()
