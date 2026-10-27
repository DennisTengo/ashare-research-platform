"""应用入口与导航。

导航放在页面顶端，只保留文字，不放图标。

文件名不能与顶层包重名：Streamlit 会把页面所在目录加入 sys.path，
若页面叫 factor.py，`import factor.factors` 会解析到页面文件而不是包，
因此这里用 factor_analysis / data_quality。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

st.set_page_config(
    page_title="A 股投研平台",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 顶部导航的字号略大于默认，并留出与内容的间距。
st.markdown(
    """
    <style>
    [data-testid="stTopNav"] a span,
    [data-testid="stSidebarNav"] a span {
        font-size: 1.02rem !important;
        letter-spacing: 0.02em;
    }
    [data-testid="stTopNav"] {
        padding-top: 0.35rem;
        padding-bottom: 0.35rem;
    }
    /* 用顶部导航时左侧栏没有内容，收起它的残留边距 */
    [data-testid="stSidebar"] {
        display: none;
    }
    /* 本地自用工具，不需要右上角的 Deploy 按钮。
       注意：不能隐藏 stToolbar 容器——顶部导航就渲染在里面，
       隐藏容器会把四个导航按钮一起干掉。只能精准藏按钮本身。 */
    [data-testid="stAppDeployButton"] {
        display: none;
    }
    /* 隐藏运行状态小浮层（File change / Rerun / Always rerun），
       这东西只在开发时有意义，盯着看盘的人只会觉得碍事。 */
    [data-testid="stStatusWidget"] {
        display: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

PAGES = [
    st.Page("views/overview.py", title="首页", default=True),
    st.Page("views/stock.py", title="个股查询"),
    st.Page("views/factor_analysis.py", title="因子回测"),
    st.Page("views/data_quality.py", title="数据质检"),
]

st.navigation(PAGES, position="top").run()
