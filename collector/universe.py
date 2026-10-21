"""股票池定义。"""

from __future__ import annotations

import duckdb

from common.logger import get_logger
from common.symbols import infer_a_share_exchange, to_symbol

log = get_logger("collector.universe")

SUPPORTED = ("hs300", "all")


def hs300_symbols() -> list[str]:
    """沪深 300 当前成分股。注意：这是最新一期的快照，不含历史成分变动。"""
    import akshare as ak

    df = ak.index_stock_cons_csindex(symbol="000300")
    if df is None or df.empty:
        raise RuntimeError("沪深 300 成分股接口返回空数据")
    codes = df["成分券代码"].astype(str).str.zfill(6).unique().tolist()
    symbols = []
    for code in codes:
        try:
            symbols.append(to_symbol(code, infer_a_share_exchange(code)))
        except ValueError:
            log.warning("跳过无法识别的成分券: {}", code)
    return sorted(set(symbols))


def all_symbols(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute("SELECT code FROM dim_stock ORDER BY code").fetchall()
    if not rows:
        raise RuntimeError("dim_stock 为空，请先运行 scripts/sync_basic.py")
    return [r[0] for r in rows]


def resolve_universe(name: str, conn: duckdb.DuckDBPyConnection) -> list[str]:
    name = name.strip().lower()
    if name in ("hs300", "csi300", "000300"):
        return hs300_symbols()
    if name == "all":
        return all_symbols(conn)
    raise ValueError(f"未支持的股票池: {name}，可选 {SUPPORTED}")
