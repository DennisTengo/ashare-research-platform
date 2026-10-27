"""图表构造。

抽成独立模块的原因：图表代码原本嵌在页面函数里，只有当用户选完股票才会执行，
所以页面级冒烟测试根本覆盖不到。结果就是一个 Candlestick 不支持的属性
（unselected，那是散点图才有的）让页面直接白屏，而测试全绿。

图表构造是纯函数：输入 DataFrame，输出 Figure，不碰 Streamlit。
这样可以直接单测，也可以复用到其他页面。
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.shared import rangebreaks

UP_COLOR = "#E45756"
DOWN_COLOR = "#54A24B"


def build_kline(
    df: pd.DataFrame, title: str, start: str, end: str, breaks: list | None = None
) -> go.Figure:
    """K 线 + 成交量双图。

    df 需含 trade_date / open / high / low / close / volume，已按日期升序。
    breaks 为 None 时按区间从交易日历算休市日；测试时传 [] 可避开数据库。
    """
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.03
    )
    fig.add_trace(
        go.Candlestick(
            x=df["trade_date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="K 线",
            increasing_line_color=UP_COLOR,
            decreasing_line_color=DOWN_COLOR,
        ),
        row=1, col=1,
    )
    colors = [UP_COLOR if c >= o else DOWN_COLOR for c, o in zip(df["close"], df["open"])]
    fig.add_trace(
        go.Bar(x=df["trade_date"], y=df["volume"], name="成交量", marker_color=colors),
        row=2, col=1,
    )
    fig.update_layout(
        # 高度必须显式给定：Plotly 在 iframe 里拿不到容器高度，
        # autosize 会退化，全屏后子图比例失调、成交量被挤出视口。
        height=760,
        title=dict(text=title, font=dict(size=13)) if title else None,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=34 if title else 8, b=10),
        showlegend=False,
        hovermode="x unified",
        dragmode="zoom",
    )
    # 压缩休市日，停牌日仍留空
    fig.update_xaxes(rangebreaks=breaks if breaks is not None else rangebreaks(start, end))
    # 价格轴跟随缩放：框选一段行情时 y 随之收窄，放大到低价区间不会再把 K 线压成一条线。
    # 之前锁死 fixedrange=True 的副作用就是全周期最高最低价一直生效。
    fig.update_yaxes(fixedrange=False, row=1, col=1)
    # 成交量轴保持固定：跟着缩放会把柱状图拉得忽高忽低，失去比较意义。
    fig.update_yaxes(fixedrange=True, row=2, col=1)
    return fig


def kline_config() -> dict:
    """Plotly 工具栏配置。

    去掉的按钮：
        zoomIn2d / zoomOut2d  以绘图区中心等比放大，连点会把底部推出视野
        select2d / lasso2d    框选数据点对 K 线无意义，且高亮逻辑是
                              「未选中变淡」，容易让人以为特效加反了
    """
    return {
        "displaylogo": False,
        # 关闭滚轮缩放：Plotly 的滚轮是「以鼠标为中心等比缩放」，同时缩 x 和 y，
        # K 线必然被拉变形。框选可以只框住想要的区间、纵轴跟着收窄，是唯一可靠的交互。
        "scrollZoom": False,
        # 双击回到初始视图，价格轴解开后需要一个明确的退路
        "doubleClick": "reset",
        "modeBarButtonsToRemove": [
            "zoomIn2d",
            "zoomOut2d",
            "autoScale2d",
            "toggleSpikelines",
            "select2d",
            "lasso2d",
        ],
    }
