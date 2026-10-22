"""行情数据源适配层。

屏蔽两个源的差异，统一输出标准列：trade_date / open / high / low / close / volume / amount

关键差异（已实测）：
- akshare(东财)：成交量单位是「手」，需要 ×100 换算成股
- baostock：成交量单位已经是「股」，不需要换算

复数口径：adjust 传 "" 不复权、"hfq" 后复权、"qfq" 前复权。
"""

from __future__ import annotations

import pandas as pd

from common.logger import get_logger
from common.symbols import split_symbol

log = get_logger("collector.sources")

STANDARD_COLUMNS = ["trade_date", "open", "high", "low", "close", "volume", "amount"]

_AKSHARE_MAP = {
    "日期": "trade_date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
}

_BAOSTOCK_FIELDS = "date,open,high,low,close,volume,amount"
_BAOSTOCK_ADJUST = {"": "3", "hfq": "1", "qfq": "2"}

_logged_in = False

# 长批量任务中 baostock 的连接会中途中断，错误信息没有错误码可判，只能按文案识别。
_NETWORK_ERROR_HINTS = ("网络", "接收", "连接", "超时", "timeout", "recv", "connect", "reset", "broken")


def _looks_like_network_error(message: str) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in _NETWORK_ERROR_HINTS)


def _ensure_baostock_login() -> None:
    global _logged_in
    if _logged_in:
        return
    import baostock as bs

    result = bs.login()
    if result.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {result.error_msg}")
    _logged_in = True
    log.debug("baostock 已登录")


def baostock_logout() -> None:
    """批量任务结束后调用。未登录时是无操作。"""
    global _logged_in
    if not _logged_in:
        return
    import baostock as bs

    bs.logout()
    _logged_in = False
    log.debug("baostock 已登出")


def _force_relogin() -> None:
    """连接异常后重建会话。

    必须先把标志位置回 False，否则 _ensure_baostock_login 会直接返回，
    导致后续每次查询都重复同一个失败。
    """
    global _logged_in
    _logged_in = False
    try:
        import baostock as bs

        bs.logout()
    except Exception:  # noqa: BLE001 - 登出失败不影响重连
        pass
    _ensure_baostock_login()


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=STANDARD_COLUMNS)


def fetch_akshare(code: str, start: str, end: str, adjust: str) -> pd.DataFrame:
    import akshare as ak

    raw = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust=adjust,
    )
    if raw is None or raw.empty:
        return _empty()

    df = raw.rename(columns=_AKSHARE_MAP)
    missing = [c for c in STANDARD_COLUMNS if c not in df.columns]
    if missing:
        raise RuntimeError(f"akshare 字段变化，缺少: {missing}（实际: {list(raw.columns)}）")

    out = df[STANDARD_COLUMNS].copy()
    for col in ("open", "high", "low", "close", "volume", "amount"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["volume"] = out["volume"] * 100  # 手 -> 股
    return out


def fetch_baostock(
    symbol: str, start: str, end: str, adjust: str, retry: bool = True
) -> pd.DataFrame:
    _ensure_baostock_login()
    import baostock as bs

    code, exchange = split_symbol(symbol)
    bs_code = f"{exchange.lower()}.{code}"
    flag = _BAOSTOCK_ADJUST.get(adjust)
    if flag is None:
        raise ValueError(f"baostock 不支持的复权口径: {adjust!r}")

    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            _BAOSTOCK_FIELDS,
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag=flag,
        )
        if rs.error_code != "0":
            raise RuntimeError(f"baostock 查询失败({bs_code}): {rs.error_msg}")

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
    except Exception as exc:  # noqa: BLE001 - 统一在此判定是否为连接问题
        # 会话中断时 error_msg 是「网络接收错误」这类文案，重建会话后重试一次即可恢复。
        # 只重试一次，避免真故障时无限递归。
        if retry and _looks_like_network_error(str(exc)):
            log.warning("baostock 会话异常（{}），重建后重试 {}", exc, symbol)
            _force_relogin()
            return fetch_baostock(symbol, start, end, adjust, retry=False)
        raise

    if not rows:
        return _empty()

    df = pd.DataFrame(rows, columns=_BAOSTOCK_FIELDS.split(","))
    out = df.rename(columns={"date": "trade_date"})[STANDARD_COLUMNS].copy()
    for col in ("open", "high", "low", "close", "volume", "amount"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def fetch(symbol: str, start: str, end: str, adjust: str, source: str) -> pd.DataFrame:
    """按数据源拉取，返回标准列。"""
    name = (source or "baostock").strip().lower()
    if name == "baostock":
        return fetch_baostock(symbol, start, end, adjust)
    if name == "akshare":
        code, _ = split_symbol(symbol)
        return fetch_akshare(code, start, end, adjust)
    raise ValueError(f"未知数据源: {source!r}")
