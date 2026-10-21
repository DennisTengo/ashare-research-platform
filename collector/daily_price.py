"""日线行情与复权因子。

口径约定：
- 价格与成交量为**不复权**原始值，入库时成交量统一为「股」；
- 复权因子 = 后复权收盘 / 不复权收盘，除权日跳升，序列非递减；
- 库中永不写入前复权价格。

输入的 DataFrame 由 sources 层提供，已是标准列。
"""

from __future__ import annotations

import duckdb
import pandas as pd

from common.logger import get_logger
from warehouse.writer import upsert

log = get_logger("collector.daily_price")

PRICE_COLUMNS = ["trade_date", "open", "high", "low", "close", "volume", "amount"]


def _empty_prices() -> pd.DataFrame:
    return pd.DataFrame(columns=["code", *PRICE_COLUMNS])


def _empty_factors() -> pd.DataFrame:
    return pd.DataFrame(columns=["code", "trade_date", "adj_factor"])


def build_price_frame(symbol: str, std: pd.DataFrame) -> pd.DataFrame:
    """标准列 -> fact_daily_price 结构。"""
    if std is None or std.empty:
        return _empty_prices()

    missing = [c for c in PRICE_COLUMNS if c not in std.columns]
    if missing:
        raise RuntimeError(f"行情数据缺少列: {missing}（实际: {list(std.columns)}）")

    out = std[PRICE_COLUMNS].copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
    out = out.dropna(subset=["trade_date"])
    if out.empty:
        return _empty_prices()

    out["volume"] = out["volume"].fillna(0).round().astype("int64")
    for col in ("open", "high", "low", "close", "amount"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out.insert(0, "code", symbol)
    return out.sort_values("trade_date").reset_index(drop=True)


def build_factor_frame(symbol: str, raw: pd.DataFrame, hfq: pd.DataFrame) -> pd.DataFrame:
    """由不复权与后复权价格计算复权因子。

    算法：逐日「复权收益率 / 原始收益率」累乘。
    不用 hfq/close 直接相除：数据源的后复权价是涨跌幅复权，相除得到的是
    每天都变化的比值，而不是只在除权日跳升的阶梯因子。

    因子以拉取区间首日为基准（=1.0），仅保证比率正确，绝对值无意义。
    因此增量更新必须从固定起点重算，不能只拉新增区间。
    """
    if raw is None or raw.empty or hfq is None or hfq.empty:
        return _empty_factors()

    a = raw[["trade_date", "close"]].rename(columns={"close": "close_raw"})
    b = hfq[["trade_date", "close"]].rename(columns={"close": "close_hfq"})
    a["trade_date"] = pd.to_datetime(a["trade_date"], errors="coerce")
    b["trade_date"] = pd.to_datetime(b["trade_date"], errors="coerce")
    a = a.dropna(subset=["trade_date"])
    b = b.dropna(subset=["trade_date"])

    merged = a.merge(b, on="trade_date", how="inner").sort_values("trade_date")
    merged = merged[(merged["close_raw"] > 0) & (merged["close_hfq"] > 0)]
    if len(merged) < 2:
        return _empty_factors()

    ret_adj = merged["close_hfq"].pct_change().fillna(0.0) + 1.0
    ret_raw = merged["close_raw"].pct_change().fillna(0.0) + 1.0
    ratio = (ret_adj / ret_raw).replace([float("inf"), float("-inf")], float("nan"))

    # 数据源不连续检测
    # 真实除权：原始价跳（分红送股），复权价连续。
    # 数据源故障：复权价大幅跳变而原始价正常。
    # 因此不能看「比值」，要看「复权价自身的单日变化」——现金分红只让原始价跌 1~3%，
    # 落在正常波动区间内，用比值为准判断会误杀。
    raw_ret = merged["close_raw"].pct_change()
    adj_ret = merged["close_hfq"].pct_change()
    wild_adj = (adj_ret < -0.15) | (adj_ret > 1.0)
    quiet_raw = raw_ret.between(-0.11, 0.11)
    broken = wild_adj & quiet_raw
    if broken.any():
        ratio = ratio.where(~broken, 1.0)

    # 因子单调性硬约束：真实除权只会让复权因子上升，任何下降都是数据噪声。
    # 这一步会吸收复权价与原始价收益率之间 1~3% 量级的偏差，避免因子回退。
    decreasing = ratio < 1.0 - 1e-6
    if decreasing.any():
        ratio = ratio.where(~decreasing, 1.0)

    # 单日比值超出 [0.1, 10] 视为数据异常，剔除避免污染后续累乘
    keep = ratio.between(0.1, 10.0) | ratio.isna()
    merged = merged[keep]
    ratio = ratio[keep].fillna(1.0)

    factor = ratio.cumprod()
    return pd.DataFrame(
        {
            "code": symbol,
            "trade_date": merged["trade_date"].to_numpy(),
            "adj_factor": factor.to_numpy(dtype="float"),
        }
    ).reset_index(drop=True)


def fetch_symbol(
    symbol: str, start: str, end: str, source: str = "baostock"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """拉取单只股票的不复权行情与复权因子。"""
    from collector.sources import fetch

    raw = fetch(symbol, start, end, "", source)
    if raw.empty:
        return _empty_prices(), _empty_factors()
    hfq = fetch(symbol, start, end, "hfq", source)
    return build_price_frame(symbol, raw), build_factor_frame(symbol, raw, hfq)


def save(conn: duckdb.DuckDBPyConnection, prices: pd.DataFrame, factors: pd.DataFrame) -> int:
    """幂等写入两张表，返回写入的行情行数。"""
    rows = 0
    if prices is not None and not prices.empty:
        rows = upsert(conn, "fact_daily_price", prices, ["code", "trade_date"])
    if factors is not None and not factors.empty:
        upsert(conn, "fact_adj_factor", factors, ["code", "trade_date"])
    return rows
