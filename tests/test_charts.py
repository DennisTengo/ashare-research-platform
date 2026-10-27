"""图表构造的单元测试。

存在的理由：图表代码原本写在页面函数里，只有用户选完股票才执行，
页面级冒烟测试覆盖不到。结果是一个 Candlestick 不支持的属性
让页面直接白屏，而 5 个页面测试全绿。

这里直接用构造好的 DataFrame 验证 Figure，不碰数据库、不起服务。
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.charts import build_kline, kline_config

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _bars(n: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    close = [10 + i * 0.1 for i in range(n)]
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": close,
            "high": [c + 0.2 for c in close],
            "low": [c - 0.2 for c in close],
            "close": close,
            "volume": [1000 + i for i in range(n)],
        }
    )


def test_build_kline_has_candlestick_and_volume():
    fig = build_kline(_bars(), "测试", "2024-01-01", "2024-12-31", breaks=[])
    assert len(fig.data) == 2
    assert fig.data[0].type == "candlestick"
    assert fig.data[1].type == "bar"


def test_build_kline_serializes_to_json():
    """序列化能抓住「属性名不支持」这类错误——正是上次页面白屏的原因。"""
    fig = build_kline(_bars(), "测试", "2024-01-01", "2024-12-31", breaks=[])
    payload = fig.to_json()
    assert "candlestick" in payload


def test_price_axis_follows_zoom():
    """价格轴必须可缩放。

    锁死纵轴会让全周期最高最低价一直生效，放大到一段低价区间后
    K 线被压成一条线。框选时纵轴跟着收窄才符合看行情的直觉。
    """
    fig = build_kline(_bars(), "测试", "2024-01-01", "2024-12-31", breaks=[])
    assert fig.layout.yaxis.fixedrange is False


def test_volume_axis_stays_fixed():
    """成交量轴保持固定。

    跟着价格轴一起缩放会把柱状图拉得忽高忽低，不同周期之间失去可比性。
    """
    fig = build_kline(_bars(), "测试", "2024-01-01", "2024-12-31", breaks=[])
    assert fig.layout.yaxis2.fixedrange is True


def test_height_is_explicit():
    """高度不能交给 autosize：iframe 里拿不到容器高度会退化。"""
    fig = build_kline(_bars(), "测试", "2024-01-01", "2024-12-31", breaks=[])
    assert fig.layout.height and fig.layout.height > 400


def test_config_removes_confusing_buttons():
    cfg = kline_config()
    removed = set(cfg["modeBarButtonsToRemove"])
    assert {"zoomIn2d", "zoomOut2d", "select2d", "lasso2d"} <= removed
    # 滚轮是「以鼠标为中心等比缩放」，同时缩 x 和 y，K 线必然被拉变形。
    # 缩放只保留框选：它能只框住时间区间，让纵轴跟着收窄。
    assert cfg["scrollZoom"] is False
    assert cfg["doubleClick"] == "reset"


def test_empty_frame_still_builds():
    """空数据不应抛异常，页面靠自己判空。"""
    empty = _bars().iloc[0:0]
    fig = build_kline(empty, "空", "2024-01-01", "2024-12-31", breaks=[])
    assert fig is not None
