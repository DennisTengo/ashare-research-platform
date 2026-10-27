"""K 线 option 构造。

守四件事：
    1. 纵轴必须 scale——这是换掉 Plotly 的唯一理由，缩放后纵轴要跟着可见区间走
    2. dataZoom 只作用于 x 轴，纵轴交给 scale 自适应，不能让缩放碰它
    3. 蜡烛数据顺序是 [开, 收, 低, 高]，与常见的 OHLC 排列不同，写错不报错、只画错
    4. 停牌日必须留空档，否则「停牌一周后跳空开盘」会被画成平滑波动

option 是纯字典，这些断言都不需要起 Streamlit。
"""

from __future__ import annotations

import pandas as pd

from app.charts import DOWN, EMPTY, UP, build_kline_option, financial_option

CALENDAR = ["2024-01-02", "2024-01-03", "2024-01-04"]


def _bars() -> pd.DataFrame:
    """三根蜡烛，第二根是跌的，便于验证涨跌配色。"""
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(CALENDAR),
            "open": [10.0, 11.0, 12.0],
            "high": [10.5, 11.5, 12.5],
            "low": [9.5, 10.5, 11.5],
            "close": [10.8, 10.5, 12.8],
            "volume": [100, 200, 300],
        }
    )


def test_y_axis_scales_to_visible_data():
    """scale=True 才能让纵轴贴合当前可见区间。"""
    option = build_kline_option(_bars())
    assert option["yAxis"][0]["scale"] is True


def test_datazoom_only_touches_x_axis():
    """dataZoom 不能带 yAxisIndex，否则缩放会直接拉伸纵轴。"""
    option = build_kline_option(_bars())
    inside = next(z for z in option["dataZoom"] if z["type"] == "inside")
    assert inside["xAxisIndex"] == [0, 1]
    assert "yAxisIndex" not in inside


def test_candle_order_is_open_close_low_high():
    option = build_kline_option(_bars())
    candles = option["series"][0]["data"]
    assert candles[0] == [10.0, 10.8, 9.5, 10.5]


def test_missing_trading_day_keeps_gap():
    """1/03 停牌：x 轴仍要有这一天，数值位置留空。"""
    bars = _bars().drop(index=1)
    option = build_kline_option(bars, CALENDAR)

    assert option["xAxis"][0]["data"] == CALENDAR
    assert option["series"][0]["data"][1] == EMPTY
    assert option["series"][1]["data"][1] == EMPTY


def test_without_calendar_uses_data_dates():
    """周/月/年线不传日历，x 轴直接取数据里的日期。"""
    option = build_kline_option(_bars())
    assert len(option["xAxis"][0]["data"]) == 3


def test_volume_color_follows_direction():
    """红涨绿跌，与 A 股习惯一致。"""
    volumes = build_kline_option(_bars())["series"][1]["data"]
    assert volumes[0]["itemStyle"]["color"] == UP    # 10.0 → 10.8
    assert volumes[1]["itemStyle"]["color"] == DOWN  # 11.0 → 10.5


def test_initial_view_shows_recent_segment_when_dense():
    """上千根全部铺开时每根蜡烛不到 1 像素，初始应只看末端一段。"""
    n = 1000
    bars = pd.DataFrame(
        {
            "trade_date": pd.date_range("2020-01-01", periods=n, freq="D"),
            "open": [10.0] * n,
            "high": [10.5] * n,
            "low": [9.5] * n,
            "close": [10.2] * n,
            "volume": [100] * n,
        }
    )
    option = build_kline_option(bars, initial_bars=100)
    inside = next(z for z in option["dataZoom"] if z["type"] == "inside")
    assert inside["start"] == 90.0
    assert inside["end"] == 100


def test_initial_view_shows_all_when_sparse():
    """数据本来就少（周/月/年线）时不应该裁掉开头。"""
    option = build_kline_option(_bars(), initial_bars=160)
    inside = next(z for z in option["dataZoom"] if z["type"] == "inside")
    assert inside["start"] == 0
    assert inside["end"] == 100


def test_toolbox_frames_zoom_on_time_axis_only():
    """框选放大必须只缩时间轴；若连 y 一起缩就回到 Plotly 拉变形的问题。"""
    option = build_kline_option(_bars())
    assert option["toolbox"]["feature"]["dataZoom"]["yAxisIndex"] == "none"


def test_financial_option_uses_two_axes():
    """营收和净利量级差一个数量级，必须分轴，否则净利被压成一条线。"""
    fin = pd.DataFrame(
        {
            "report_period": pd.to_datetime(["2024-03-31", "2024-06-30"]),
            "revenue": [1e10, 2e10],
            "net_profit": [1e9, 3e9],
        }
    )
    option = financial_option(fin)
    assert len(option["yAxis"]) == 2
    assert option["series"][1]["yAxisIndex"] == 1


def test_financial_option_converts_to_yi_and_labels_quarters():
    fin = pd.DataFrame(
        {
            "report_period": pd.to_datetime(["2024-03-31", "2024-12-31"]),
            "revenue": [1e10, 2e10],
            "net_profit": [1e9, 3e9],
        }
    )
    option = financial_option(fin)
    assert option["xAxis"]["data"] == ["24Q1", "24Q4"]
    assert option["series"][0]["data"] == [100.0, 200.0]  # 元 → 亿
    assert option["series"][1]["data"] == [10.0, 30.0]


def test_financial_option_handles_empty_frame():
    empty = pd.DataFrame(columns=["report_period", "revenue", "net_profit"])
    assert financial_option(empty)["series"] == []
