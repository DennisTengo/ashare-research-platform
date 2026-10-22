"""界面冒烟测试。

目的不是测样式，而是保证每个页面在真实数据库上「能跑完不抛异常」。
界面最常见的故障是某个空结果没处理导致整页崩掉，这类问题只靠肉眼点一遍
很容易漏，而且每次改数据层都可能让页面悄悄坏掉。

AppTest 会真实执行页面脚本，捕获未处理异常。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def _run(page: str) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app" / page), default_timeout=180)
    at.run()
    return at


def test_overview_page_runs():
    at = _run("main.py")
    assert not at.exception, at.exception


def test_stock_page_runs():
    at = _run("pages/1_stock.py")
    assert not at.exception, at.exception


def test_factor_page_renders_without_running():
    """未点「运行分析」时页面应正常展示表单，而不是空转或报错。"""
    at = _run("pages/2_factor.py")
    assert not at.exception, at.exception


def test_quality_page_renders_without_running():
    at = _run("pages/3_quality.py")
    assert not at.exception, at.exception


def test_stock_page_has_metrics():
    """首页应有指标卡，说明数据读出来了。"""
    at = _run("main.py")
    assert len(at.metric) >= 5
