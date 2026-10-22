"""回测结果存档。

一次回测写一条记录，参数和指标都以 JSON 保存——这样以后新增参数或指标
不需要改表结构，历史记录也始终可比。

存的是「这一轮跑的是什么、出来什么数」，不存中间数据。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import duckdb

from common.logger import get_logger

log = get_logger("analysis.record")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def current_commit() -> str:
    """取当前代码版本（短 hash）。取不到时返回 unknown，不影响存档。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 - 版本号只是附加信息
        return "unknown"


def record_backtest(
    conn: duckdb.DuckDBPyConnection,
    factor: str,
    label: str,
    params: dict,
    results: list[dict],
) -> int:
    """写入一条回测记录，返回 record id。

    results 是该次回测里每个组合的绩效（基准、各组、多空），
    每个元素至少含 label 与各项指标。
    """
    record_id = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO meta_backtest (id, git_commit, factor, label, params, metrics) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            record_id,
            current_commit(),
            factor,
            label,
            json.dumps(params, ensure_ascii=False),
            json.dumps({"results": results}, ensure_ascii=False),
        ],
    )
    log.info("回测已存档：{} / {}（{} 个组合）", factor, label, len(results))
    return record_id


def load_history(
    conn: duckdb.DuckDBPyConnection, factor: str | None = None, limit: int = 20
) -> list[dict]:
    """读取历史回测记录，供对比用。"""
    sql = "SELECT id, run_at, git_commit, factor, label, params, metrics FROM meta_backtest"
    params: list = []
    if factor:
        sql += " WHERE factor = ?"
        params.append(factor)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": r[0],
            "run_at": r[1],
            "git_commit": r[2],
            "factor": r[3],
            "label": r[4],
            "params": json.loads(r[5]),
            "metrics": json.loads(r[6]),
        }
        for r in rows
    ]
