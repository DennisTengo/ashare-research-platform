"""界面冒烟测试。

目的不是测样式，而是保证每个页面在真实数据库上「能跑完不抛异常」。
界面最常见的故障是某个空结果没处理导致整页崩掉，这类问题只靠肉眼点一遍
很容易漏，而且每次改数据层都可能让页面悄悄坏掉。

AppTest 会真实执行页面脚本，捕获未处理异常。

页面脚本放在 views/ 下，入口 main.py 通过 st.navigation 组织它们。
两边都要测：单页测内容，入口测导航装配是否成立。
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def _run(page: str, params: dict | None = None) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app" / page), default_timeout=180)
    for key, value in (params or {}).items():
        at.query_params[key] = value
    at.run()
    return at


def test_entry_navigation_runs():
    """入口文件用 st.navigation 装配页面，能跑完不异常即说明页面清单有效。"""
    at = _run("main.py")
    assert not at.exception, at.exception


def test_overview_page_runs():
    at = _run("views/overview.py")
    assert not at.exception, at.exception


def test_stock_page_runs():
    at = _run("views/stock.py")
    assert not at.exception, at.exception


def test_factor_library_page_renders():
    """因子库列表页读取离线档案，正常应出现表格与状态说明。"""
    at = _run("views/factor_library.py")
    assert not at.exception, at.exception
    assert len(at.dataframe) >= 1


def test_factor_detail_page_renders():
    """带 ?factor= 时进详情页：指标卡、四张图、三组表格都要出来。

    这条同时验证「详情页只读档案」这个约束能跑通——如果页面偷偷去重算，
    会在这里碰到库锁或变慢。
    """
    at = _run("views/factor_library.py", {"factor": "volatility_20d"})
    assert not at.exception, at.exception
    assert len(at.metric) >= 6
    assert len(at.dataframe) >= 3
    assert any("volatility_20d" in t.value for t in at.title)
    assert any("返回因子库" in b.label for b in at.button)


def test_factor_detail_falls_back_to_list_for_unknown_name():
    """URL 里写了一个不存在的因子，应该回到列表页，而不是空白页。"""
    at = _run("views/factor_library.py", {"factor": "no_such_factor"})
    assert not at.exception, at.exception
    assert len(at.dataframe) >= 1
    assert any("因子库" in t.value for t in at.title)


def test_quality_page_renders_without_running():
    at = _run("views/data_quality.py")
    assert not at.exception, at.exception


def test_backtest_page_renders_without_running():
    """策略回测页未点击运行时只展示表单与偏差警示。"""
    at = _run("views/backtest.py")
    assert not at.exception, at.exception


def test_backtest_page_runs_and_shows_metrics():
    """点「运行回测」后要出指标卡和绩效表。

    这条会真的跑满 79 期，是整个页面的主路径：只测未运行状态，
    等于把结果区（图表、绩效表、成交明细）全部漏掉。
    """
    at = _run("views/backtest.py")
    at.button[0].click().run()
    assert not at.exception, at.exception
    assert len(at.metric) >= 5
    assert len(at.dataframe) >= 2


def test_stock_page_has_metrics():
    """首页应有指标卡，说明数据读出来了。"""
    at = _run("views/overview.py")
    assert len(at.metric) >= 5
