"""财务数据采集。

数据源：baostock 的三类财务接口。
    query_profit_data    盈利能力（ROE、净利率、毛利率、净利润、EPS、主营收入）
    query_growth_data    成长能力（权益/资产/净利润同比）
    query_balance_data   偿债能力（流动比率、资产负债率、权益乘数）

**PIT 对齐的关键**：
    baostock 每条记录同时返回 pubDate（公告日）和 statDate（报告期）。
    对外的对齐锚点必须是 pubDate——2024Q1 报告期是 3-31，但 4-27 才公告，
    在 4-15 就用它等于偷看未来。
    本模块只负责把两个日期都原样存下，对齐逻辑由查询层负责。

已知缺口：
    baostock 不直接提供总资产与净资产总额（只有资产负债率与权益乘数）。
    这两列先留空，后续需要用 akshare 的资产负债表补充。
"""

from __future__ import annotations

import json

import duckdb
import pandas as pd

from common.logger import get_logger
from common.symbols import split_symbol

log = get_logger("collector.financial")

QUARTERS = (1, 2, 3, 4)
_QUARTER_END = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


def quarter_end(year: int, quarter: int) -> str:
    """季度对应的报告期日期字符串。"""
    return f"{year}-{_QUARTER_END[quarter]}"

# 三个接口的字段，用于把值映射到表列
_PROFIT_MAP = {
    "roeAvg": "roe",
    "npMargin": "net_margin",
    "gpMargin": "gross_margin",
    "netProfit": "net_profit",
    "epsTTM": "eps",
    "MBRevenue": "revenue",
}
_GROWTH_MAP = {
    "YOYEquity": "yoy_equity",
    "YOYAsset": "yoy_asset",
    "YOYNI": "yoy_net_profit",
}
_BALANCE_MAP = {
    "currentRatio": "current_ratio",
    "quickRatio": "quick_ratio",
    "liabilityToAsset": "liability_to_asset",
    "assetToEquity": "asset_to_equity",
}


def _ensure_login() -> None:
    from collector.sources import _ensure_baostock_login

    _ensure_baostock_login()


def _query(api: str, bs_code: str, year: int, quarter: int) -> dict:
    """调用一个财务接口，返回第一条记录（这些接口每季度只返回一条）。"""
    import baostock as bs

    result = getattr(bs, api)(code=bs_code, year=year, quarter=quarter)
    if result.error_code != "0":
        raise RuntimeError(f"{api}({bs_code}, {year}Q{quarter}) 失败: {result.error_msg}")
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    if not rows:
        return {}
    return dict(zip(result.fields, rows[0]))


def _to_float(value) -> float | None:
    """baostock 用空字符串表示缺失，统一转成 None。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_quarter(symbol: str, year: int, quarter: int) -> dict | None:
    """拉取一只股票某一季度的财务数据，合并三类指标。

    没有数据（例如当时尚未上市）时返回 None。
    """
    _ensure_login()
    code, exchange = split_symbol(symbol)
    bs_code = f"{exchange.lower()}.{code}"

    profit = _query("query_profit_data", bs_code, year, quarter)
    if not profit or not profit.get("statDate"):
        return None

    growth = _query("query_growth_data", bs_code, year, quarter)
    balance = _query("query_balance_data", bs_code, year, quarter)

    record: dict = {
        "code": symbol,
        "report_period": profit["statDate"],
        "announce_date": profit["pubDate"],
    }
    extra: dict = {}

    for source, mapping in ((profit, _PROFIT_MAP), (growth, _GROWTH_MAP), (balance, _BALANCE_MAP)):
        for src_key, target in mapping.items():
            value = _to_float(source.get(src_key))
            if target in ("roe", "net_margin", "gross_margin", "net_profit", "eps", "revenue"):
                record[target] = value
            else:
                extra[target] = value

    record["total_assets"] = None
    record["total_equity"] = None
    record["extra"] = json.dumps(extra, ensure_ascii=False)
    return record


def fetch_symbol(symbol: str, years: list[int]) -> list[dict]:
    """拉取一只股票多个年度、四个季度的全部数据。"""
    records: list[dict] = []
    for year in years:
        for quarter in QUARTERS:
            try:
                record = fetch_quarter(symbol, year, quarter)
            except Exception as exc:  # noqa: BLE001 - 单季失败不打断整只股票
                log.warning("{} {}Q{} 拉取失败: {}", symbol, year, quarter, exc)
                continue
            if record:
                records.append(record)
    return records


def save(conn: duckdb.DuckDBPyConnection, records: list[dict]) -> int:
    """写入财务数据，按主键去重（同报告期重复公告时保留每一次）。"""
    if not records:
        return 0
    df = pd.DataFrame(records)
    for col in (
        "revenue", "net_profit", "total_assets", "total_equity",
        "roe", "eps", "gross_margin", "net_margin", "extra",
    ):
        if col not in df.columns:
            df[col] = None
    df = df[
        [
            "code", "report_period", "announce_date", "revenue", "net_profit",
            "total_assets", "total_equity", "roe", "eps", "gross_margin",
            "net_margin", "extra",
        ]
    ]
    conn.register("_fin_tmp", df)
    conn.execute(
        """
        INSERT OR REPLACE INTO fact_financial
        SELECT CAST(code AS VARCHAR), CAST(report_period AS DATE), CAST(announce_date AS DATE),
               CAST(revenue AS DOUBLE), CAST(net_profit AS DOUBLE),
               CAST(total_assets AS DOUBLE), CAST(total_equity AS DOUBLE),
               CAST(roe AS DOUBLE), CAST(eps AS DOUBLE),
               CAST(gross_margin AS DOUBLE), CAST(net_margin AS DOUBLE),
               CAST(extra AS JSON)
        FROM _fin_tmp
        """
    )
    conn.unregister("_fin_tmp")
    return len(df)
