"""校验结果的汇总与落库。

汇总只做计数和分类，不重新计算——问题明细已经在 Finding 里。
"""

from __future__ import annotations

import time

import duckdb

from common.logger import get_logger
from quality.checks import Finding

log = get_logger("quality.report")

_RULE_LABELS = {
    "price_invalid": "价格非法",
    "duplicate": "重复记录",
    "factor_monotonic": "复权因子下降",
    "return_limit": "涨跌幅超限",
    "date_gap": "交易日断档",
    "zero_volume_price_move": "零成交但价格变动",
}

# 这些规则的问题是真实的市场现象，不是数据错误。
_NON_FATAL = {"return_limit", "date_gap", "zero_volume_price_move"}


def summarize(results: dict[str, list[Finding]]) -> list[dict]:
    """按规则汇总数量与严重度。"""
    out = []
    for rule, findings in results.items():
        errors = sum(1 for f in findings if f.severity == "error")
        warnings = len(findings) - errors
        out.append(
            {
                "rule": rule,
                "label": _RULE_LABELS.get(rule, rule),
                "count": len(findings),
                "errors": errors,
                "warnings": warnings,
                "fatal": rule not in _NON_FATAL,
            }
        )
    return sorted(out, key=lambda x: (not x["fatal"], -x["count"]))


def save_findings(conn: duckdb.DuckDBPyConnection, results: dict[str, list[Finding]]) -> int:
    """清空旧结果并写入本次校验。返回写入条数。"""
    conn.execute("DELETE FROM meta_dq_result")
    base = int(time.time() * 1000)
    records = []
    for rule, findings in results.items():
        for f in findings:
            records.append((base + len(records), rule, f.code, f.trade_date, f.detail))
    if records:
        conn.executemany(
            "INSERT INTO meta_dq_result (id, rule, code, trade_date, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            records,
        )
    log.info("校验结果已写入 meta_dq_result：{} 条", len(records))
    return len(records)


def format_report(
    results: dict[str, list[Finding]], sample: int = 3, truncated: bool = False
) -> str:
    """生成可读的文本报告。

    truncated 为 True 时提示条数可能被截断，避免把「最多返回 500 条」
    误读成真实总数。
    """
    lines = []
    for item in summarize(results):
        flag = "错误" if item["fatal"] else "提示"
        lines.append(f"[{flag}] {item['label']}（{item['rule']}）: {item['count']} 条")
        findings = results.get(item["rule"], [])[:sample]
        for f in findings:
            lines.append(f"    {f.code} {f.trade_date} — {f.detail}")
        if item["count"] > sample:
            suffix = "（已达单规则上限，实际可能更多）" if truncated and item["count"] >= 500 else ""
            lines.append(f"    …… 另有 {item['count'] - sample} 条{suffix}")
    return "\n".join(lines) if lines else "未发现问题"
