"""图表构造（Apache ECharts）。

为什么从 Plotly 换过来：
    Plotly 的缩放是「以鼠标为中心、同时缩 x 和 y」，纵轴范围要么被锁死
    （放大到低价区间时 K 线被压成一条线），要么跟着等比缩放（K 线被拉变形）。
    两种都不是看行情要的手感，而 Plotly 没有「缩放时纵轴重算」这个能力。

    ECharts 把两件事拆开了：dataZoom 只作用于 x 轴，yAxis.scale 让纵轴
    按当前可见数据重算。缩放后纵轴自动贴合可见区间，这是它的原生行为，
    不需要任何事件回调。

option 是纯数据字典，不依赖 Streamlit，可以直接单测。
Streamlit 1.66 起内置 st.echarts_chart，不需要第三方组件。
"""

from __future__ import annotations

import pandas as pd

# A 股配色：红涨绿跌（与欧美相反）
UP = "#E5484D"
DOWN = "#12A594"
GRID_LINE = "rgba(128, 128, 128, 0.18)"
AXIS_TEXT = "#8A8A8A"
EMPTY = "-"  # ECharts 约定的空值标记


def build_kline_option(
    df: pd.DataFrame,
    calendar: list[str] | None = None,
    initial_bars: int = 160,
) -> dict:
    """K 线 + 成交量的 ECharts option。

    参数：
        df            含 trade_date / open / high / low / close / volume，按日期升序
        calendar      完整交易日序列。传入时缺失日期会留成空档，停牌不会被连成曲线；
                      周/月/年线不需要传，聚合后本来就稀疏，日期直接取自数据。
        initial_bars  初始视图展示的末端根数。全部历史（1000+ 根）一次性铺满时，
                      每根蜡烛不到 1 像素，看起来就只剩一条线。默认只看最近一段，
                      剩下的交给缩放滑块或滚轮。

    停牌留空这条不能省：库里的行情是按「有交易才有行」存的，如果 x 轴直接用
    数据里的日期，停牌日会消失，「停牌一周后跳空开盘」会被画成平滑波动，
    等于造假。
    """
    frame = df.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])

    if calendar:
        index = pd.DatetimeIndex(pd.to_datetime(calendar), name="trade_date")
        frame = frame.set_index("trade_date").reindex(index).reset_index()

    dates = frame["trade_date"].dt.strftime("%Y-%m-%d").tolist()

    # 初始视图只看末端一段，否则蜡烛密到不可见
    total = len(dates)
    start_pct = round((1 - initial_bars / total) * 100, 2) if total > initial_bars else 0

    candles: list = []
    volumes: list = []
    for open_, close, low, high, volume in zip(
        frame["open"], frame["close"], frame["low"], frame["high"], frame["volume"]
    ):
        if pd.isna(open_):
            candles.append(EMPTY)
            volumes.append(EMPTY)
            continue
        # ECharts 的蜡烛顺序固定为 [开, 收, 低, 高]，与常见的 OHLC 不同
        candles.append(
            [round(float(open_), 2), round(float(close), 2),
             round(float(low), 2), round(float(high), 2)]
        )
        color = UP if close >= open_ else DOWN
        volumes.append({"value": float(volume), "itemStyle": {"color": color}})

    return {
        "animation": False,
        "backgroundColor": "transparent",
        "textStyle": {"fontFamily": "inherit"},
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "cross"},
            "backgroundColor": "rgba(32, 32, 32, 0.88)",
            "borderWidth": 0,
            "textStyle": {"color": "#EDEDED", "fontSize": 12},
        },
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        # 框选放大：点工具栏的放大镜图标后拖出一个矩形，只缩时间轴，纵轴交给 scale 重算。
        # 不直接拦截鼠标右键：st.echarts_chart 不支持 JS 回调，拿不到原始鼠标事件。
        "toolbox": {
            "show": True,
            "right": 10,
            "top": 4,
            "itemSize": 13,
            "iconStyle": {"borderColor": AXIS_TEXT},
            "feature": {
                "dataZoom": {
                    "yAxisIndex": "none",
                    "title": {"zoom": "框选放大", "back": "取消框选"},
                },
                "restore": {"title": "还原"},
            },
        },
        "grid": [
            # top 留到 26 给右上角的工具栏图标腾位置，否则第一个图标会压在 K 线上
            {"left": 58, "right": 14, "top": 26, "height": "58%"},
            {"left": 58, "right": 14, "top": "72%", "height": "14%"},
        ],
        "xAxis": [
            {
                "type": "category",
                "data": dates,
                "gridIndex": 0,
                "boundaryGap": True,
                "axisLine": {"lineStyle": {"color": GRID_LINE}},
                "axisTick": {"show": False},
                "axisLabel": {"show": False},
                "splitLine": {"show": False},
            },
            {
                "type": "category",
                "data": dates,
                "gridIndex": 1,
                "boundaryGap": True,
                "axisLine": {"lineStyle": {"color": GRID_LINE}},
                "axisTick": {"show": False},
                "axisLabel": {"color": AXIS_TEXT, "fontSize": 11},
                "splitLine": {"show": False},
            },
        ],
        "yAxis": [
            {
                # scale=True 是关键：纵轴不从 0 起算，而是贴合当前可见数据的范围。
                # 缩放时 ECharts 会自动重算，这是 Plotly 做不到的。
                "scale": True,
                "gridIndex": 0,
                "axisLine": {"show": False},
                "axisTick": {"show": False},
                "axisLabel": {"color": AXIS_TEXT, "fontSize": 11},
                "splitLine": {"lineStyle": {"color": GRID_LINE}},
            },
            {
                "scale": True,
                "gridIndex": 1,
                "splitNumber": 2,
                "axisLine": {"show": False},
                "axisTick": {"show": False},
                "axisLabel": {"show": False},
                "splitLine": {"show": False},
            },
        ],
        "dataZoom": [
            # inside：滚轮缩放，只作用于 x，纵轴交给 scale 自适应
            {
                "type": "inside",
                "xAxisIndex": [0, 1],
                "start": start_pct,
                "end": 100,
            },
            # slider：底部区间条，一眼看到当前视图在整段历史里的位置
            {
                "type": "slider",
                "xAxisIndex": [0, 1],
                "bottom": 6,
                "height": 16,
                "start": start_pct,
                "end": 100,
                "showDetail": False,
                "borderColor": "transparent",
                "fillerColor": "rgba(128, 128, 128, 0.15)",
                "handleStyle": {"color": "#9A9A9A"},
                "textStyle": {"color": AXIS_TEXT, "fontSize": 10},
            },
        ],
        "series": [
            {
                "name": "K线",
                "type": "candlestick",
                "data": candles,
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "itemStyle": {
                    "color": UP,
                    "color0": DOWN,
                    "borderColor": UP,
                    "borderColor0": DOWN,
                    "borderWidth": 1,
                },
            },
            {
                "name": "成交量",
                "type": "bar",
                "data": volumes,
                "xAxisIndex": 1,
                "yAxisIndex": 1,
            },
        ],
    }


def bar_option(
    x: list,
    y: list,
    *,
    color: str = "#4C78A8",
    height: str = "320px",
    ylabel: str = "",
) -> dict:
    """单系列柱状图。用于月度入库量、分组收益、IC 时序这类简单统计。"""
    return {
        "animation": False,
        "backgroundColor": "transparent",
        "textStyle": {"fontFamily": "inherit"},
        "grid": {"left": 62, "right": 18, "top": 18, "bottom": 42},
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "shadow"},
            "backgroundColor": "rgba(32, 32, 32, 0.88)",
            "borderWidth": 0,
            "textStyle": {"color": "#EDEDED", "fontSize": 12},
        },
        "xAxis": {
            "type": "category",
            "data": [str(v) for v in x],
            "axisLine": {"lineStyle": {"color": GRID_LINE}},
            "axisTick": {"show": False},
            "axisLabel": {"color": AXIS_TEXT, "fontSize": 11},
        },
        "yAxis": {
            "type": "value",
            "name": ylabel,
            "nameTextStyle": {"color": AXIS_TEXT, "fontSize": 11},
            "axisLine": {"show": False},
            "axisTick": {"show": False},
            "axisLabel": {"color": AXIS_TEXT, "fontSize": 11},
            "splitLine": {"lineStyle": {"color": GRID_LINE}},
        },
        "series": [
            {
                "type": "bar",
                "data": [float(v) for v in y],
                "itemStyle": {"color": color},
                "barMaxWidth": 28,
            }
        ],
    }


def financial_option(df: pd.DataFrame, periods: int = 12) -> dict:
    """营收与净利润的季度对比。

    两个量级差一个数量级，共用一根 y 轴会把净利润压成一条线，所以用双轴：
    左轴营收，右轴净利。

    注意：营收只有半年报和年报是数据源原始值，一季报和三季报是按
    「净利润 ÷ 净利率」反推的，误差已验过但仍是估算值。
    """
    if df.empty:
        return {"series": []}

    view = df.sort_values("report_period").tail(periods)

    labels = []
    for period in view["report_period"]:
        stamp = pd.Timestamp(period)
        labels.append(f"{stamp.year % 100:02d}Q{(stamp.month - 1) // 3 + 1}")

    def _to_yi(series) -> list:
        return [round(float(v) / 1e8, 2) if pd.notna(v) else None for v in series]

    return {
        "animation": False,
        "backgroundColor": "transparent",
        "textStyle": {"fontFamily": "inherit"},
        "grid": {"left": 40, "right": 40, "top": 34, "bottom": 24},
        "legend": {
            "top": 2,
            "itemWidth": 10,
            "itemHeight": 7,
            "textStyle": {"fontSize": 10, "color": AXIS_TEXT},
        },
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "shadow"},
            "backgroundColor": "rgba(32, 32, 32, 0.88)",
            "borderWidth": 0,
            "textStyle": {"color": "#EDEDED", "fontSize": 11},
        },
        "xAxis": {
            "type": "category",
            "data": labels,
            "axisLine": {"lineStyle": {"color": GRID_LINE}},
            "axisTick": {"show": False},
            "axisLabel": {"color": AXIS_TEXT, "fontSize": 9, "interval": 0},
        },
        "yAxis": [
            {
                "type": "value",
                "axisLine": {"show": False},
                "axisTick": {"show": False},
                "axisLabel": {"color": AXIS_TEXT, "fontSize": 9},
                "splitLine": {"lineStyle": {"color": GRID_LINE}},
            },
            {
                "type": "value",
                "axisLine": {"show": False},
                "axisTick": {"show": False},
                "axisLabel": {"color": AXIS_TEXT, "fontSize": 9},
                "splitLine": {"show": False},
            },
        ],
        "series": [
            {
                "name": "营收(亿)",
                "type": "bar",
                "data": _to_yi(view["revenue"]),
                "itemStyle": {"color": "#4C78A8"},
                "barMaxWidth": 12,
            },
            {
                "name": "净利(亿)",
                "type": "bar",
                "yAxisIndex": 1,
                "data": _to_yi(view["net_profit"]),
                "itemStyle": {"color": UP},
                "barMaxWidth": 12,
            },
        ],
    }


def line_option(
    x: list,
    series: dict[str, list],
    *,
    height: str = "380px",
    ylabel: str = "",
) -> dict:
    """多系列折线图。用于累计净值这类多曲线对比。"""
    return {
        "animation": False,
        "backgroundColor": "transparent",
        "textStyle": {"fontFamily": "inherit"},
        "grid": {"left": 62, "right": 18, "top": 46, "bottom": 56},
        "legend": {
            "top": 8,
            "textStyle": {"color": AXIS_TEXT, "fontSize": 11},
            "itemWidth": 14,
            "itemHeight": 8,
        },
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "rgba(32, 32, 32, 0.88)",
            "borderWidth": 0,
            "textStyle": {"color": "#EDEDED", "fontSize": 12},
        },
        "xAxis": {
            "type": "category",
            "data": [str(v) for v in x],
            "axisLine": {"lineStyle": {"color": GRID_LINE}},
            "axisTick": {"show": False},
            "axisLabel": {"color": AXIS_TEXT, "fontSize": 11},
        },
        "yAxis": {
            "type": "value",
            "name": ylabel,
            "scale": True,
            "nameTextStyle": {"color": AXIS_TEXT, "fontSize": 11},
            "axisLine": {"show": False},
            "axisTick": {"show": False},
            "axisLabel": {"color": AXIS_TEXT, "fontSize": 11},
            "splitLine": {"lineStyle": {"color": GRID_LINE}},
        },
        "series": [
            {
                "name": name,
                "type": "line",
                "data": [round(float(v), 4) for v in values],
                "showSymbol": False,
                "lineWidth": 1.6,
            }
            for name, values in series.items()
        ],
    }
