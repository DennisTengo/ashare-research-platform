"""股票列表采集。

只做在市股票；退市股由后续单独任务补齐（数据源质量较差，避免阻塞主流程）。
代码统一标准化为 ``600000.SH`` 形式，B 股排除。
"""

from __future__ import annotations

import duckdb
import pandas as pd

from common.logger import get_logger
from common.symbols import is_b_share, split_symbol, to_symbol
from warehouse.writer import upsert

log = get_logger("collector.stock_list")


def infer_board(symbol: str) -> str:
    """按代码与交易所判断板块。"""
    code, exchange = split_symbol(symbol)
    if exchange == "BJ":
        return "北交所"
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301", "302")):
        return "创业板"
    return "主板"


def _safe_to_symbol(code: str) -> str | None:
    """单个代码识别失败时返回 None，由调用方过滤，避免打断整批。"""
    try:
        return to_symbol(code)
    except ValueError:
        return None


def _normalize_date(value):
    """上市日期格式不统一：上交所/北交所给 date，深交所给字符串，统一成 date。"""
    ts = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def fetch_list_dates() -> dict[str, object]:
    """从三个交易所名单拉取上市日，返回 {代码: 上市日}。

    缺上市日会让「剔除次新股」这类股票池条件无法实现，
    也无法计算上市天数，因此单独补齐。接口失败不阻断主流程。
    """
    import akshare as ak

    sources = (
        ("stock_info_sh_name_code", "证券代码", "上市日期", {"symbol": "主板A股"}),
        # 科创板在上交所接口里是单独的 symbol，不传参数就漏掉 618 只
        ("stock_info_sh_name_code", "证券代码", "上市日期", {"symbol": "科创板"}),
        ("stock_info_sz_name_code", "A股代码", "A股上市日期", {}),
        ("stock_info_bj_name_code", "证券代码", "上市日期", {}),
    )
    mapping: dict[str, object] = {}
    for fn_name, code_col, date_col, kwargs in sources:
        try:
            df = getattr(ak, fn_name)(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 单一交易所失败不影响其余
            log.warning("{}{} 获取失败: {}", fn_name, kwargs or "", exc)
            continue
        if code_col not in df.columns or date_col not in df.columns:
            log.warning("{} 字段变化: {}", fn_name, list(df.columns))
            continue
        for raw_code, raw_date in zip(df[code_col], df[date_col]):
            symbol = _safe_to_symbol(str(raw_code).zfill(6))
            day = _normalize_date(raw_date)
            if symbol and day:
                mapping[symbol] = day
    log.info("上市日获取完成: {} 条", len(mapping))
    return mapping


def fetch_stock_list() -> pd.DataFrame:
    """拉取全部在市 A 股，输出 dim_stock 可直接写入的结构。"""
    import akshare as ak

    raw = ak.stock_info_a_code_name()
    if raw is None or raw.empty:
        raise RuntimeError("股票列表接口返回空数据")
    if not {"code", "name"}.issubset(raw.columns):
        raise RuntimeError(f"股票列表接口字段变化: {list(raw.columns)}")

    df = raw.rename(columns={"code": "raw_code"})[["raw_code", "name"]].copy()
    df["raw_code"] = df["raw_code"].astype(str).str.zfill(6)

    before = len(df)
    df = df[~df["raw_code"].map(is_b_share)].copy()
    if before != len(df):
        log.info("已排除 B 股 {} 只", before - len(df))

    df["code"] = df["raw_code"].map(_safe_to_symbol)
    unknown = df[df["code"].isna()]
    if len(unknown):
        log.warning("跳过 {} 只无法识别的代码: {}", len(unknown), list(unknown["raw_code"][:5]))
        df = df.dropna(subset=["code"])

    df["board"] = df["code"].map(infer_board)
    df["is_st"] = df["name"].str.contains("ST", case=False, na=False)
    df = df.drop(columns=["raw_code"]).drop_duplicates(subset=["code"])

    # 上市日：缺失会导致「剔除次新股」无法实现，也拿不到上市天数
    dates = fetch_list_dates()
    df["list_date"] = df["code"].map(dates)
    missing = int(df["list_date"].isna().sum())
    if missing:
        log.warning("{} 只股票未拿到上市日", missing)

    return df


def sync_stock_list(conn: duckdb.DuckDBPyConnection) -> int:
    """全量对齐在市股票列表，幂等。"""
    df = fetch_stock_list()
    rows = upsert(conn, "dim_stock", df, keys=["code"])
    log.info("股票列表同步完成: {} 行", rows)
    return rows
