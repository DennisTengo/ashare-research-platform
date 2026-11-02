"""因子库：列表 + 详情。

对应需求 FR-4.7（因子快照报告）、FR-4.8（因子详情页）。

这一页只读：所有数字都来自 meta_factor_report 里已经算好的档案，页面不做任何统计。
算是由 scripts/compute_factor_reports.py 离线做的——因为 DuckDB 的库文件是独占锁的，
界面进程一旦持有只读连接，任何写连接都打不开，所以「谁写」只能是独立脚本。

列表与详情用 ?factor=<名称> 区分，而不是两个导航页：
    详情本质上属于某个因子，做成页面就得让人在两个标签之间来回找自己是哪个因子。
    带参数的 URL 还能直接贴给别人（虽然现在只有你自己用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder

from analysis.factor_report import STATUS_FAILED, STATUS_MISSING, STATUS_READY, STATUS_STALE
from app.charts import line_option
from app.factor_detail import (
    clicked_factor,
    flat_curve,
    group_table,
    ic_yearly_table,
    nav_curve,
    portfolio_table,
    settings_table,
    summary,
    yearly_table,
)
from app.shared import factor_library

COMMAND = r".venv\Scripts\python.exe scripts\compute_factor_reports.py"

STATUS_HELP = {
    STATUS_READY: "参数与代码都和档案一致",
    STATUS_STALE: "因子代码或参数变过，库里那份还是旧口径，需要重算",
    STATUS_MISSING: "还没有档案",
    STATUS_FAILED: "最后一次计算报错，原因见下方",
}

YEARLY_SERIES = ("多空(净)", "多头(净)", "基准(等权)", "IC")

# 列宽（像素）。不靠自动列宽：自动宽会把「说明」拉到半屏，
# 把数字列挤成省略号；固定宽宁可横向滚动。
_COLUMN_WIDTHS = {
    "因子": 165,
    "说明": 250,
    "持有期": 80,
    "分组": 70,
    "状态": 90,
    "多空年化%": 105,
    "多空夏普": 95,
    "多头年化%": 105,
    "多头回撤%": 110,
    "IC 均值": 90,
    "IC 胜率%": 95,
    "换手%": 85,
    "期数": 70,
    "计算时间": 100,
}


def _portfolios(record: dict) -> dict[str, dict]:
    rows = (record or {}).get("metrics", {}).get("portfolios") or []
    return {row.get("label"): row for row in rows}


def _pct(value, digits: int = 2):
    return None if value is None else round(value * 100, digits)


def _num(value, digits: int = 2):
    return None if value is None else round(value, digits)


def _row(item: dict) -> dict:
    record = item["report"]
    portfolios = _portfolios(record)
    long_leg = portfolios.get(f"G{item['groups']}", {})
    spread = portfolios.get("多空(净)", {})
    ic = (record or {}).get("metrics", {}).get("ic") or {}
    return {
        "因子": item["name"],
        "说明": item["description"],
        "持有期": item["horizon"],
        "分组": item["groups"],
        "状态": item["status"],
        "多空年化%": _pct(spread.get("annual_return")),
        "多空夏普": _num(spread.get("sharpe")),
        "多头年化%": _pct(long_leg.get("annual_return")),
        "多头回撤%": _pct(long_leg.get("max_drawdown")),
        "IC 均值": _num(ic.get("ic_mean"), 4),
        "IC 胜率%": _pct(ic.get("win_rate"), 1),
        "换手%": _pct(long_leg.get("turnover"), 1),
        "期数": (record or {}).get("n_periods"),
        "计算时间": (
            record["computed_at"].strftime("%m-%d %H:%M")
            if record and record.get("computed_at")
            else None
        ),
    }


def _open(name: str) -> None:
    st.query_params["factor"] = name
    st.rerun()


def _grid(table: pd.DataFrame):
    """因子列表。双击一行进入详情。

    为什么不用 st.dataframe：它的行选择带一个勾选框列，而且点击即选中，
    复制数字时很容易误触。换成 AgGrid 后取消勾选框，只有双击才进详情。
    代价是多一个前端依赖，以及表格样式与其它页的 st.dataframe 不完全一致。
    """
    builder = GridOptionsBuilder.from_dataframe(table)
    builder.configure_default_column(sortable=True, filter=False, resizable=True)
    builder.configure_selection(selection_mode="single", use_checkbox=False)
    builder.configure_grid_options(suppressMovableColumns=True)
    for column, width in _COLUMN_WIDTHS.items():
        if column in table.columns:
            builder.configure_column(column, width=width)
    return AgGrid(
        table,
        gridOptions=builder.build(),
        theme="streamlit",
        height=None,  # 自动高度：5 行不该占满一屏，以后加因子也不必改这个数
        update_on=["cellDoubleClicked"],
        show_search=False,
        show_download_button=False,
        key="factor_grid",
    )


def _list_view(library: list[dict]) -> None:
    st.title("因子库")

    ready = sum(1 for item in library if item["status"] == STATUS_READY)
    st.caption(
        f"共 {len(library)} 个因子，其中 {ready} 个已算出档案。"
        "每个因子一份快照：参数、代码指纹、指标、曲线一起存，页面只读、不现算，"
        "所以同一屏上的数字必然同源。"
    )

    table = pd.DataFrame([_row(item) for item in library])
    response = _grid(table)
    st.caption("双击一行进详情；单击只选中，不会跳转。")
    picked = clicked_factor(response.event_data, table)
    if picked:
        _open(picked)

    pending = [item for item in library if item["status"] != STATUS_READY]
    if pending:
        st.warning(
            "有 {} 个因子的档案不是最新的：{}。".format(
                len(pending), "、".join(f"{i['name']}（{i['status']}）" for i in pending)
            )
        )
        for item in [i for i in pending if i["status"] == STATUS_FAILED]:
            st.error(f"{item['name']}：{(item['report'] or {}).get('error') or '未记录原因'}")

    with st.expander("状态说明与如何计算", expanded=not pending):
        st.dataframe(
            [{"状态": k, "含义": v} for k, v in STATUS_HELP.items()],
            use_container_width=True,
            hide_index=True,
        )
        st.markdown(
            "计算在命令行里跑，界面不提供按钮：DuckDB 的库文件是独占锁的，"
            "页面持有只读连接时写连接打不开。脚本会自动跳过参数与代码都没变的因子："
        )
        st.code(COMMAND, language="powershell")
        st.caption(
            "参数归因子所有（写在 factor/factors/*.py 里）。改了 horizon 或分组数，"
            "就是另一份档案，旧的那份仍然留着，历史数字不会被覆盖掉。"
        )

    st.caption(
        "口径：沪深 300 成分股（当前快照，有幸存者偏差）｜ T+1 收盘成交 ｜ 后复权 ｜ "
        "基准为池内等权 ｜ 成本 买入 0.13%、卖出 0.23%。"
        "多空组合受融券限制，只作因子有效性的理论度量。"
    )


def _metric_cards(record: dict) -> None:
    values = summary(record)
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("多空年化", f"{values['多空年化']:+.2%}" if values["多空年化"] is not None else "—")
    c2.metric("多空夏普", f"{values['多空夏普']:.2f}" if values["多空夏普"] is not None else "—")
    c3.metric("多头年化", f"{values['多头年化']:+.2%}" if values["多头年化"] is not None else "—")
    c4.metric("多头回撤", f"{values['多头回撤']:.2%}" if values["多头回撤"] is not None else "—")
    c5.metric("IC 均值", f"{values['IC 均值']:+.4f}" if values["IC 均值"] is not None else "—")
    c6.metric("IC 胜率", f"{values['IC 胜率']:.1%}" if values["IC 胜率"] is not None else "—")


def _charts(record: dict) -> None:
    dates, curves = nav_curve(record)
    left, right = st.columns(2)
    with left:
        st.echarts_chart(line_option(dates, curves, height="300px", ylabel="净值"), height=300)
    with right:
        _, sharpe = flat_curve(record, "rolling_sharpe")
        st.echarts_chart(
            line_option(dates, {"滚动夏普（一年窗口）": sharpe}, height="300px", ylabel="夏普"),
            height=300,
        )

    left, right = st.columns(2)
    with left:
        _, turnover = flat_curve(record, "turnover")
        st.echarts_chart(
            line_option(
                dates,
                {"换手": [None if v is None else round(v * 100, 2) for v in turnover]},
                height="260px",
                ylabel="换手 %",
            ),
            height=260,
        )
    with right:
        _, ic = flat_curve(record, "ic")
        st.echarts_chart(
            line_option(dates, {"IC": ic}, height="260px", ylabel="IC"), height=260
        )

    st.caption(
        "曲线按调仓日画，不是日频：一期一个点，横轴间隔就是持有期。"
        "滚动夏普前几期为空，因为一年窗口还没走满。"
    )


def _detail_view(item: dict) -> None:
    record = item["report"]
    name = item["name"]

    if st.button("← 返回因子库"):
        del st.query_params["factor"]
        st.rerun()

    st.title(name)
    st.caption(item["description"])

    if record is None:
        st.warning("这个因子还没有档案。运行下面的命令生成：")
        st.code(f"{COMMAND} --factor {name}", language="powershell")
        return

    params = record.get("params") or {}
    metrics = record.get("metrics") or {}
    st.caption(
        f"状态 {item['status']} ｜ {record.get('n_periods')} 期 ｜ "
        f"{metrics.get('first_date')} ~ {metrics.get('last_date')} ｜ "
        f"代码指纹 {record.get('code_hash')} ｜ "
        f"计算于 {record.get('computed_at'):%Y-%m-%d %H:%M}"
    )
    if item["status"] == STATUS_STALE:
        st.warning(
            "这份档案的口径已经不是当前代码/参数的产物（" + STATUS_HELP[STATUS_STALE] + "）。"
            "下面的数字仍然有效，但只代表算它的那一刻。"
        )
    elif item["status"] == STATUS_FAILED:
        st.error(f"上次计算失败：{record.get('error') or '未记录原因'}")

    _metric_cards(record)
    _charts(record)

    tab_groups, tab_yearly, tab_meta = st.tabs(["分层与绩效", "年度表现", "设置与代码"])

    with tab_groups:
        st.markdown("**分层收益**（G1 因子值最低，最后一组最高）")
        st.dataframe(group_table(record), use_container_width=True, hide_index=True)
        st.markdown("**绩效明细**（含多空与基准）")
        st.dataframe(portfolio_table(record), use_container_width=True, hide_index=True)
        st.caption(
            "累计与年化是同一份逐期收益的两种读法；波动与回撤按调仓期序列算。"
            "多空(净) 已扣双边成本，多空(毛) 未扣。"
        )

    with tab_yearly:
        series = st.radio(
            "序列", YEARLY_SERIES, horizontal=True, key=f"yearly_{name}",
            label_visibility="collapsed",
        )
        table = ic_yearly_table(record) if series == "IC" else yearly_table(record, series)
        st.dataframe(table, use_container_width=True, hide_index=True)
        st.caption(
            "按调仓日所在自然年归属。各年收益复利拼接等于全样本累计——"
            "年度表与上面的曲线用的是同一份逐期收益，对不上就是 bug。"
        )

    with tab_meta:
        st.markdown("**Simulation Settings**")
        st.dataframe(settings_table(params), use_container_width=True, hide_index=True)
        st.markdown("**因子代码**")
        archived = record.get("source")
        current_hash = item.get("current_code_hash")
        if archived:
            st.code(archived, language="python")
            if current_hash and current_hash != record.get("code_hash"):
                st.caption("上面是算这份档案时的代码，与当前代码不同（状态为需重算）。")
        else:
            st.caption("这份档案没存源码（早于该字段加入），重算一次就有了。")


def main() -> None:
    library = factor_library()
    if not library:
        st.error("没有注册任何因子。检查 factor/factors/ 下的文件是否注册成功。")
        return

    by_name = {item["name"]: item for item in library}
    selected = st.query_params.get("factor")
    if selected and selected in by_name:
        _detail_view(by_name[selected])
        return

    _list_view(library)


main()