"""因子库首页。

对应需求 FR-4.7（因子快照报告）。

这一页只读：所有数字都来自 meta_factor_report 里已经算好的档案，页面不做任何统计。
算是由 scripts/compute_factor_reports.py 离线做的——因为 DuckDB 的库文件是独占锁的，
界面进程一旦持有只读连接，任何写连接都打不开，所以「谁写」只能是独立脚本。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from analysis.factor_report import (
    STATUS_FAILED,
    STATUS_MISSING,
    STATUS_READY,
    STATUS_STALE,
)
from app.shared import factor_library

COMMAND = r".venv\Scripts\python.exe scripts\compute_factor_reports.py"

STATUS_HELP = {
    STATUS_READY: "参数与代码都和档案一致",
    STATUS_STALE: "因子代码或参数变过，库里那份还是旧口径，需要重算",
    STATUS_MISSING: "还没有档案",
    STATUS_FAILED: "最后一次计算报错，原因见下方",
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
            record["computed_at"].strftime("%m-%d %H:%M") if record and record.get("computed_at") else None
        ),
    }


def main() -> None:
    st.title("因子库")

    library = factor_library()
    if not library:
        st.error("没有注册任何因子。检查 factor/factors/ 下的文件是否注册成功。")
        return

    ready = sum(1 for item in library if item["status"] == STATUS_READY)
    st.caption(
        f"共 {len(library)} 个因子，其中 {ready} 个已算出档案。"
        "每个因子一份快照：参数、代码指纹、指标、曲线一起存，页面只读、不现算，"
        "所以同一屏上的数字必然同源。"
    )

    table = pd.DataFrame([_row(item) for item in library])
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "说明": st.column_config.TextColumn(width="medium"),
            "状态": st.column_config.TextColumn(width="small"),
            "多空年化%": st.column_config.NumberColumn(format="%.2f", help="最高分组减最低分组，扣双边成本"),
            "多头年化%": st.column_config.NumberColumn(format="%.2f", help="只做多最高分组"),
            "IC 均值": st.column_config.NumberColumn(format="%+.4f"),
            "期数": st.column_config.NumberColumn(width="small"),
        },
    )

    pending = [item for item in library if item["status"] != STATUS_READY]
    if pending:
        st.warning(
            "有 {} 个因子的档案不是最新的：{}。".format(
                len(pending), "、".join(f"{i['name']}（{i['status']}）" for i in pending)
            )
        )
        failed = [item for item in pending if item["status"] == STATUS_FAILED]
        for item in failed:
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
        "细节看某一个因子：详情页在下一轮补上。"
    )


main()