"""个股查询页。

布局：左侧 K 线主图，右侧报价与资料面板。
对应需求 FR-3.1（K 线与成交量、周期与复权切换）、FR-3.3（财务指标表）、
FR-3.4（基本信息卡）。

K 线默认展示全部历史，周期切换（日/周/月/年）由 analysis.resample 聚合，
复权换算在界面层完成：库里只有原始价和因子，不存前复权价。

未实现的字段：总市值、流通市值、换手率、市盈率需要股本数据，
库里尚未采集，面板上不展示，避免出现空值或错误数字。
"""

from __future__ import annotations

import datetime as dt
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from analysis.resample import to_period
from app.charts import build_kline, kline_config
from app.shared import (
    apply_adjust,
    financials,
    missing_trading_days,
    price_bars,
    stock_options,
)

# Plotly 的框选轮廓是单个 path 里的两个环（贴边外环 + 拖拽内环），
# 一描边就会同时画出两条虚线。而它的填充用 fill-rule="evenodd"，
# 实际被涂的是「两个环之间的区域」——看起来就是拖拽区两侧变灰。
# 选择器必须直接命中 path，中间加层级（如 .zoomlayer）会匹配不到。
st.markdown(
    """
    <style>
    /* 个股页把左右留白压到最小，横向空间让给 K 线。
       这个页面的信息密度最高，通用宽度的边距在这里是纯浪费。 */
    .block-container {
        padding-left: 1.1rem !important;
        padding-right: 0.5rem !important;
        /* 顶部导航是固定定位（约 3.75rem 高），padding 小于它会把第一行控件顶到导航下面。
           默认值是 6rem，这里压到 4.2rem，既有安全间距又不浪费纵向空间。 */
        padding-top: 4.2rem !important;
        max-width: 100% !important;
    }

    .js-plotly-plot path.zoombox {
        fill: transparent !important;
        stroke: #C8C8C8 !important;
        stroke-width: 2px !important;
        /* 单个 path 里包含两个环：贴边外环 + 拖拽内环，一描边就会同时画出两条。
           用 clip-path 从元素边缘向内裁 1.5px，把外环的线切掉，只留内环。 */
        clip-path: inset(1.5px) !important;
    }
    .js-plotly-plot path.zoombox-corners {
        fill: #C8C8C8 !important;
        stroke: #C8C8C8 !important;
        stroke-width: 0 !important;
    }

    /* ---- 右侧报价面板 ---- */
    .qp { padding: 0.1rem 0.1rem 0 0.1rem; }
    .qp-head { display: flex; align-items: baseline; gap: 0.6rem; flex-wrap: wrap; }
    .qp-name { font-size: 1.45rem; font-weight: 650; line-height: 1.2; }
    .qp-code { font-size: 0.95rem; opacity: 0.6; letter-spacing: 0.04em; }
    .qp-price { font-size: 2.05rem; font-weight: 700; line-height: 1.1; margin-top: 0.55rem; }
    .qp-chg { font-size: 0.98rem; margin-bottom: 0.85rem; }
    .qp-sec {
        font-size: 0.76rem; letter-spacing: 0.1em; opacity: 0.55;
        margin: 0.95rem 0 0.3rem 0; text-transform: uppercase;
    }
    .qp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.1rem 0.7rem; }
    .qp-item {
        display: flex; justify-content: space-between; gap: 0.5rem;
        padding: 0.22rem 0; border-bottom: 1px solid rgba(128, 128, 128, 0.16);
        font-size: 0.9rem;
    }
    .qp-item span { opacity: 0.6; white-space: nowrap; }
    .qp-item b { font-weight: 550; white-space: nowrap; }
    .up { color: #E5484D; }
    .down { color: #12A594; }
    .flat { color: #8A8A8A; }
    </style>
    """,
    unsafe_allow_html=True,
)

PERIODS = {"日K": "D", "周K": "W", "月K": "M", "年K": "Y"}
ADJUST = {"不复权": "raw", "前复权": "forward", "后复权": "back"}


# 曾尝试用前端脚本接管纵轴（img.onerror 注入，监听 plotly_relayout 后按可见 x 重算 y）。
# 实测不成立：Streamlit 打包的 plotly 不暴露 window.Plotly，脚本静默退出，
# 而定时器仍在反复尝试绑定，反而干扰了框选。已撤掉。
# 现在的策略：关闭滚轮缩放（它是中心等比缩放，必然拉扁 K 线），只保留框选。


def _search(stocks, current: str = "", compact: bool = False) -> str:
    """代码与名称合一的下拉搜索。

    Streamlit 的 selectbox 自带输入过滤，比「先输入框再下拉」两步操作
    更接近搜索引擎的体验，也避免两个控件状态不同步的问题。

    compact=True 时收成一行高的窄控件，用于选中股票后的顶栏，
    把纵向空间让给 K 线。
    """
    labels = [f"{r.code}  {r.name}" for r in stocks.itertuples()]

    index = None
    if current:
        for i, label in enumerate(labels):
            if label.startswith(current + "  "):
                index = i
                break

    if compact:
        picked = st.selectbox(
            "换一只股票",
            options=labels,
            index=index,
            key="picker",
            label_visibility="collapsed",
        )
    else:
        picked = st.selectbox(
            "搜索代码或名称",
            options=labels,
            index=index,
            key="picker",
            placeholder="输入代码或名称，例如 600519 或 茅台",
        )

    if not picked:
        return ""
    # 代码里不含空格，用双空格分隔可避开“万  科Ａ”这类名称自带空格的情况
    return picked.split("  ")[0]


def _num(value, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.{digits}f}"


def _pct(value, digits: int = 2) -> str:
    """比率转百分数。缺失时只返回破折号，不拼百分号。"""
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value) * 100:.{digits}f}%"


def _quote_panel(row, latest, prev, fin_row, period_label: str) -> None:
    """右侧报价与资料面板。数字全部在此格式化，模板里只做拼接。"""
    close = float(latest["close"])
    pclose = float(prev["close"]) if prev is not None else close
    change = close - pclose
    pct = (change / pclose * 100) if pclose else 0.0
    tone = "up" if pct > 0 else ("down" if pct < 0 else "flat")
    sign = "+" if change > 0 else ""

    high, low = float(latest["high"]), float(latest["low"])
    amplitude = ((high - low) / pclose * 100) if pclose else 0.0
    volume = float(latest["volume"]) if pd.notna(latest["volume"]) else 0.0
    amount = float(latest["amount"]) if pd.notna(latest["amount"]) else 0.0

    day = str(latest["trade_date"])[:10]
    # 注意：Series 的 .name 是索引标签，不是 name 列，必须用 row["name"]
    name = html.escape(str(row["name"]))
    code = html.escape(str(row["code"]))
    board = html.escape(str(row["board"] or "—"))
    listed = str(row["list_date"])[:10] if pd.notna(row["list_date"]) else "未知"
    status = "ST" if bool(row["is_st"]) else "正常"

    fin_items = ""
    if fin_row is not None:
        fin_items = (
            f'<div class="qp-item"><span>ROE</span><b>{_pct(fin_row.get("roe"))}</b></div>'
            f'<div class="qp-item"><span>EPS</span><b>{_num(fin_row.get("eps"))}</b></div>'
            f'<div class="qp-item"><span>毛利率</span><b>{_pct(fin_row.get("gross_margin"))}</b></div>'
            f'<div class="qp-item"><span>净利率</span><b>{_pct(fin_row.get("net_margin"))}</b></div>'
        )

    st.markdown(
        f"""
        <div class="qp">
          <div class="qp-head">
            <span class="qp-name">{name}</span>
            <span class="qp-code">{code}</span>
          </div>
          <div class="qp-price {tone}">{_num(close)}</div>
          <div class="qp-chg {tone}">{sign}{_num(change)}　{sign}{pct:.2f}%</div>

          <div class="qp-sec">最新{period_label} · {day}</div>
          <div class="qp-grid">
            <div class="qp-item"><span>今开</span><b>{_num(latest["open"])}</b></div>
            <div class="qp-item"><span>昨收</span><b>{_num(pclose)}</b></div>
            <div class="qp-item"><span>最高</span><b>{_num(high)}</b></div>
            <div class="qp-item"><span>最低</span><b>{_num(low)}</b></div>
            <div class="qp-item"><span>振幅</span><b>{amplitude:.2f}%</b></div>
            <div class="qp-item"><span>板块</span><b>{board}</b></div>
            <div class="qp-item"><span>成交量</span><b>{volume / 1e6:,.1f} 万手</b></div>
            <div class="qp-item"><span>成交额</span><b>{amount / 1e8:,.2f} 亿</b></div>
          </div>

          {"<div class='qp-sec'>最新财务</div><div class='qp-grid'>" + fin_items + "</div>" if fin_items else ""}

          <div class="qp-sec">资料</div>
          <div class="qp-grid">
            <div class="qp-item"><span>上市日</span><b>{listed}</b></div>
            <div class="qp-item"><span>状态</span><b>{status}</b></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _financial_table(fin: pd.DataFrame) -> None:
    st.subheader("财务指标")
    if fin.empty:
        st.info("该股票暂无财务数据")
        return

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


def main() -> None:
    stocks = stock_options()
    if stocks.empty:
        st.error("股票表为空")
        return

    current = st.session_state.get("code", "")

    # 还没选股票时，搜索框占满一行，作为入口；选中之后收进右上角。
    if not current:
        code = _search(stocks)
        if code:
            st.session_state["code"] = code
            st.rerun()
        st.caption("输入代码或名称开始查询。左侧为 K 线与成交量，右侧为报价与资料。")
        return

    bar_left, bar_right = st.columns([3.6, 1], gap="medium")
    with bar_left:
        # 两个控件都用分段控件：高度一致才好对齐，下拉框会把控件行拉高
        c1, c2 = st.columns([1.3, 1])
        with c1:
            period_label = st.segmented_control("周期", list(PERIODS), default="日K") or "日K"
        with c2:
            adjust_label = (
                st.segmented_control("复权", list(ADJUST), default="前复权") or "前复权"
            )
    with bar_right:
        picked = _search(stocks, current=current, compact=True)

    if picked and picked != current:
        st.session_state["code"] = picked
        current = picked

    row = stocks[stocks["code"] == current].iloc[0]

    start, end = "1990-01-01", dt.date.today().isoformat()
    bars = price_bars(current, start, end)
    if bars.empty:
        st.info("该股票没有行情数据")
        return

    view = apply_adjust(bars, ADJUST[adjust_label])
    view = to_period(view, PERIODS[period_label])
    latest = view.iloc[-1]
    prev = view.iloc[-2] if len(view) > 1 else None

    fin = financials(current)
    fin_row = fin.iloc[0].to_dict() if not fin.empty else None

    left, right = st.columns([3.5, 0.85], gap="small")

    with left:
        # 周/月/年线本身没有连续的日历缺口，注入 rangebreaks 反而会错位
        breaks = None if period_label == "日K" else []
        # 不传标题：股票名、周期、复权状态在右侧面板和控件上都已经有了，
        # 再占一行标题只会把 K 线往下挤。
        st.plotly_chart(
            build_kline(view, "", start, end, breaks=breaks),
            use_container_width=True,
            config=kline_config(),
        )

    with right:
        _quote_panel(row, latest, prev, fin_row, period_label)

    if period_label == "日K":
        missing = missing_trading_days(current, start, end)
        if missing:
            st.caption(
                f"区间内有 {len(missing)} 个交易日无数据（停牌或未上市），"
                "图上仍保留空档，未按休市日压缩。"
            )

    if ADJUST[adjust_label] == "forward":
        st.caption("前复权以最后一日为基准，最新价格与不复权一致；切换周期时历史价格随之变化。")

    st.divider()
    _financial_table(fin)


main()
