"""日线行情与复权因子的处理逻辑测试（不联网）。"""

from __future__ import annotations

import pandas as pd
import pytest

from collector.daily_price import build_factor_frame, build_price_frame, save
from common.db import count_rows


def _std_frame() -> pd.DataFrame:
    """标准列结构，volume 单位已是「股」。"""
    return pd.DataFrame(
        {
            "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "open": [7.10, 7.05, 7.00],
            "high": [7.15, 7.10, 7.25],
            "low": [7.00, 6.95, 6.98],
            "close": [7.05, 7.00, 7.20],
            "volume": [10_000_000, 12_000_000, 9_000_000],
            "amount": [705000.0, 840000.0, 648000.0],
        }
    )


def _hfq_no_event() -> pd.DataFrame:
    """与原始价等比的后复权价，期间无除权。"""
    return pd.DataFrame(
        {
            "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "close": [14.10, 14.00, 14.40],
        }
    )


def _hfq_with_split() -> pd.DataFrame:
    """第三日 10 送 10：原始价腰斩，后复权价保持不变。"""
    return pd.DataFrame(
        {
            "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "close": [14.10, 14.00, 14.00],
        }
    )


def _raw_with_split() -> pd.DataFrame:
    frame = _std_frame()
    frame.loc[frame.index[-1], "close"] = 3.50
    return frame


def test_build_price_frame_maps_columns():
    df = build_price_frame("600000.SH", _std_frame())
    assert list(df.columns) == [
        "code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ]
    assert (df["code"] == "600000.SH").all()
    assert df["trade_date"].is_monotonic_increasing


def test_volume_kept_as_shares():
    """标准列约定 volume 已是股，不得再乘 100。"""
    df = build_price_frame("600000.SH", _std_frame())
    assert df["volume"].tolist() == [10_000_000, 12_000_000, 9_000_000]


def test_volume_filled_when_missing():
    std = _std_frame()
    std.loc[std.index[1], "volume"] = None
    df = build_price_frame("600000.SH", std)
    assert df["volume"].tolist()[1] == 0


def test_build_price_frame_empty_input():
    assert build_price_frame("600000.SH", pd.DataFrame()).empty


def test_build_price_frame_missing_column_raises():
    broken = _std_frame().drop(columns=["amount"])
    with pytest.raises(RuntimeError, match="缺少列"):
        build_price_frame("600000.SH", broken)


def test_factor_flat_without_corporate_action():
    """无除权时，复权因子应恒等于 1。"""
    df = build_factor_frame("600000.SH", _std_frame(), _hfq_no_event())
    assert len(df) == 3
    assert df["adj_factor"].round(10).tolist() == [1.0, 1.0, 1.0]


def test_factor_jumps_on_split():
    """10 送 10 当日，原始收益率 -50%、复权收益率 0%，因子应翻倍。"""
    df = build_factor_frame("600000.SH", _raw_with_split(), _hfq_with_split())
    values = df["adj_factor"].round(6).tolist()
    assert values[0] == 1.0
    assert values[1] == 1.0
    assert values[2] == pytest.approx(2.0, rel=1e-6)


def test_factor_is_non_decreasing():
    df = build_factor_frame("600000.SH", _raw_with_split(), _hfq_with_split())
    assert df["adj_factor"].is_monotonic_increasing


def test_factor_ignores_source_discontinuity():
    """原始价格正常波动但后复权序列突降，视为数据源不连续，比值应置 1。"""
    hfq = _hfq_no_event()
    hfq.loc[hfq.index[-1], "close"] = 11.00  # 后复权价异常下跌 -21%，而原始价当日上涨 2.86%
    df = build_factor_frame("600000.SH", _std_frame(), hfq)
    assert df["adj_factor"].round(6).tolist() == [1.0, 1.0, 1.0]


def test_factor_keeps_real_split():
    """真实除权（原始价跌幅超出正常范围）必须保留，不能被当成数据异常修正。"""
    df = build_factor_frame("600000.SH", _raw_with_split(), _hfq_with_split())
    assert df["adj_factor"].round(6).tolist()[-1] == pytest.approx(2.0, rel=1e-6)


def test_factor_never_decreases():
    """复权价与原始价收益率小幅偏差导致因子回退时，应被强制拉平。"""
    hfq = _hfq_no_event()
    hfq.loc[hfq.index[-1], "close"] = 13.70  # 复权价 -2.14%，而原始价 +2.86%
    df = build_factor_frame("600000.SH", _std_frame(), hfq)
    assert df["adj_factor"].is_monotonic_increasing
    assert df["adj_factor"].round(6).tolist()[-1] == 1.0


def test_factor_requires_at_least_two_rows():
    single_raw = _std_frame().head(1)
    single_hfq = _hfq_no_event().head(1)
    assert build_factor_frame("600000.SH", single_raw, single_hfq).empty


def test_factor_fixes_extreme_jump_instead_of_dropping():
    """后复权价荒谬跳升但原始价正常：判定为数据源不连续，修正为 1 而非删行。"""
    hfq = _hfq_no_event()
    hfq.loc[hfq.index[-1], "close"] = 1400.0
    df = build_factor_frame("600000.SH", _std_frame(), hfq)
    assert len(df) == 3
    assert df["adj_factor"].round(6).tolist() == [1.0, 1.0, 1.0]


def test_factor_empty_when_no_overlap():
    hfq = pd.DataFrame({"trade_date": ["1999-01-04", "1999-01-05"], "close": [1.0, 1.1]})
    assert build_factor_frame("600000.SH", _std_frame(), hfq).empty


def test_save_is_idempotent(schema_db):
    prices = build_price_frame("600000.SH", _std_frame())
    factors = build_factor_frame("600000.SH", _std_frame(), _hfq_no_event())

    save(schema_db, prices, factors)
    first = count_rows(schema_db, "fact_daily_price")
    save(schema_db, prices, factors)

    assert count_rows(schema_db, "fact_daily_price") == first == 3
    assert count_rows(schema_db, "fact_adj_factor") == 3


def test_save_skips_empty_frames(schema_db):
    assert save(schema_db, pd.DataFrame(), pd.DataFrame()) == 0
    assert count_rows(schema_db, "fact_daily_price") == 0
