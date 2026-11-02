"""因子详情页的数据整形。

这些函数把库里的档案字典变成表格和曲线的输入，不碰 Streamlit，
所以下面的断言都不需要起界面。

守四件事：
    1. 小数 → 百分数的换算只做一次，不重复乘 100
    2. 缺失的值保持缺失，不要变成 0（0% 超额和「算不出来」是两件事）
    3. 三条净值曲线必须对齐到同一组日期，长度一致
    4. 空档案不能崩——新注册的因子本来就没有数字
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.factor_detail import (
    SERIES_LABELS,
    flat_curve,
    group_table,
    ic_yearly_table,
    nav_curve,
    portfolio_table,
    settings_table,
    summary,
    yearly_table,
)

DATES = ["2024-01-31", "2024-02-29", "2024-03-29"]


def _report() -> dict:
    """一份最小但结构完整的档案，字段名与 factor_report.build_report 一致。"""
    return {
        "factor_name": "demo",
        "params": {
            "horizon": 20,
            "groups": 5,
            "min_listed": 120,
            "commission": 0.001,
            "trade_price": "T+1 收盘",
            "universe": None,
        },
        "n_periods": 3,
        "code_hash": "abc123",
        "source": "def compute(panel, ctx):\n    return panel  # demo",
        "computed_at": datetime(2026, 11, 2, 20, 30),
        "metrics": {
            "first_date": "2024-01-31",
            "last_date": "2024-03-29",
            "portfolios": [
                {
                    "label": "G1",
                    "periods": 3,
                    "total_return": -0.10,
                    "annual_return": -0.30,
                    "annual_vol": 0.20,
                    "sharpe": -1.5,
                    "max_drawdown": -0.12,
                    "calmar": -2.5,
                    "win_rate": 0.3333,
                    "turnover": 0.50,
                },
                {
                    "label": "G5",
                    "periods": 3,
                    "total_return": 0.25,
                    "annual_return": 0.75,
                    "annual_vol": 0.22,
                    "sharpe": 3.4,
                    "max_drawdown": -0.05,
                    "calmar": 15.0,
                    "win_rate": 0.6667,
                    "turnover": 0.60,
                },
                {
                    "label": "多空(净)",
                    "periods": 3,
                    "total_return": 0.12,
                    "annual_return": 0.36,
                    "annual_vol": 0.10,
                    "sharpe": 3.6,
                    "max_drawdown": -0.03,
                    "calmar": 12.0,
                    "win_rate": 0.6667,
                    "turnover": None,
                },
                {
                    "label": "基准(等权)",
                    "periods": 3,
                    "total_return": 0.05,
                    "annual_return": 0.15,
                    "annual_vol": 0.18,
                    "sharpe": 0.83,
                    "max_drawdown": -0.08,
                    "calmar": 1.9,
                    "win_rate": 0.6667,
                    "turnover": None,
                },
            ],
            "ic": {"ic_mean": -0.0282, "ic_std": 0.17, "icir": -0.30, "win_rate": 0.47},
        },
        "yearly": {
            "多空(净)": [
                {
                    "year": 2024,
                    "periods": 3,
                    "total_return": 0.12,
                    "annual_return": 0.36,
                    "annual_vol": 0.10,
                    "sharpe": 3.6,
                    "max_drawdown": -0.03,
                    "win_rate": 0.6667,
                }
            ],
            "多头(净)": [],
            "基准(等权)": [],
            "IC": [
                {
                    "year": 2024,
                    "periods": 3,
                    "ic_mean": 0.0123,
                    "ic_std": 0.15,
                    "icir": 0.08,
                    "win_rate": 0.55,
                }
            ],
        },
        "curve": {
            "dates": DATES,
            "pnl": {
                "多空(净)": [1.00, 1.05, 1.12],
                "多头(净)": [1.00, 1.10, 1.25],
                "基准(等权)": [1.00, 1.02, 1.05],
            },
            "turnover": [None, 0.60, 0.60],
            "ic": [-0.05, 0.02, -0.07],
            "rolling_sharpe": [None, None, 3.6],
        },
    }


def test_settings_table_uses_chinese_labels_and_marks_unset():
    table = settings_table(_report()["params"])
    pairs = dict(zip(table["设置"], table["取值"]))
    assert pairs["持有期（交易日）"] == 20
    assert pairs["分组数"] == 5
    # 股票池没传就是「全部」，不能显示 None 让人以为漏了一项
    assert pairs["股票池"] == "全部"
    assert pairs["成交价"] == "T+1 收盘"


def test_settings_table_survives_missing_params():
    assert list(settings_table(None)["取值"].unique()) == ["全部"]


def test_portfolio_table_converts_to_percent_once():
    table = portfolio_table(_report())
    row = table[table["组合"] == "G5"].iloc[0]
    assert row["累计"] == 25.0
    assert row["年化"] == 75.0
    assert row["夏普"] == 3.4
    assert row["胜率"] == 66.67


def test_portfolio_table_keeps_missing_turnover_empty():
    """多空组合没有换手率，这里必须是空，写 0 会被当成「零换手」读。

    pandas 会把 None 升成浮点列里的 NaN，界面两种都是空白，
    所以断的是「空」而不是「None」——能区别于 0 就够了。
    """
    table = portfolio_table(_report())
    row = table[table["组合"] == "多空(净)"].iloc[0]
    assert pd.isna(row["换手"])


def test_nav_curve_series_align_with_dates():
    dates, curves = nav_curve(_report())
    assert dates == DATES
    assert list(curves) == list(SERIES_LABELS)
    assert all(len(values) == len(dates) for values in curves.values())


def test_nav_curve_skips_absent_series():
    record = _report()
    del record["curve"]["pnl"]["多头(净)"]
    _, curves = nav_curve(record)
    assert list(curves) == ["多空(净)", "基准(等权)"]


def test_flat_curve_keeps_leading_none():
    dates, sharpe = flat_curve(_report(), "rolling_sharpe")
    assert dates == DATES
    assert sharpe == [None, None, 3.6]


def test_summary_reads_top_group_and_spread():
    values = summary(_report())
    assert values["多空年化"] == 0.36
    assert values["多空夏普"] == 3.6
    # 多头腿取最高分组，不是 G1
    assert values["多头年化"] == 0.75
    assert values["多头回撤"] == -0.05
    assert values["IC 均值"] == -0.0282
    assert values["IC 胜率"] == 0.47


def test_summary_follows_group_count():
    """分组数改成 3 之后，多头腿应是 G3，而不是死写 G5。"""
    record = _report()
    record["params"]["groups"] = 3
    record["metrics"]["portfolios"].append(
        {"label": "G3", "periods": 3, "annual_return": 0.5, "sharpe": 2.0}
    )
    assert summary(record)["多头年化"] == 0.5


def test_group_table_only_lists_groups():
    table = group_table(_report())
    assert list(table["组合"]) == ["G1", "G5"]


def test_yearly_table_selects_series():
    table = yearly_table(_report(), "多空(净)")
    assert table.iloc[0]["年份"] == 2024
    assert table.iloc[0]["收益"] == 12.0
    # 这条序列是空的，返回空表而不是崩
    assert yearly_table(_report(), "多头(净)").empty


def test_ic_yearly_table_keeps_four_decimals():
    table = ic_yearly_table(_report())
    assert table.iloc[0]["IC 均值"] == 0.0123
    assert table.iloc[0]["IC 胜率"] == 55.0


def test_empty_record_does_not_crash():
    """因素刚注册、还没算过时，record 是 None 或空字典。"""
    empty: dict = {}
    assert portfolio_table(empty).empty
    assert group_table(empty).empty
    assert yearly_table(empty, "多空(净)").empty
    assert ic_yearly_table(empty).empty
    assert nav_curve(empty) == ([], {})
    assert flat_curve(empty, "ic") == ([], [])
    assert settings_table(None).shape[0] > 0
    assert all(value is None for value in summary(empty).values())