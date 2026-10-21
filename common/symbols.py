"""证券代码标准化。

统一格式：``<代码>.<交易所>``，例如 ``600000.SH``、``000001.SZ``、``430047.BJ``。

交易所是不写死的字符串，未来接入美股只用 ``AAPL.US``、港股 ``00700.HK``，
不需要改动表结构或调用方。
"""

from __future__ import annotations

# A 股常见 B 股前缀，采集时应排除。
B_SHARE_PREFIXES = ("200", "900")


def infer_a_share_exchange(code: str) -> str:
    """根据 6 位数字代码推断交易所。

    采用首位归类而非前缀枚举：交易所会新增号段（如创业板的 302），
    枚举式写法一旦遇到新号段就会把整批采集打断。
    """
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"非法 A 股代码: {code!r}")

    if code.startswith("900"):  # 沪 B
        return "SH"
    if code.startswith("200"):  # 深 B
        return "SZ"
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    if code.startswith(("4", "8")):  # 北交所
        return "BJ"
    if code.startswith(("92", "93")):  # 北交所新号段
        return "BJ"
    raise ValueError(f"无法识别的代码前缀: {code!r}")


def to_symbol(code: str, exchange: str | None = None) -> str:
    """生成标准代码。A 股可省略 exchange，由前缀推断。"""
    code = code.strip().upper()
    if exchange is None:
        exchange = infer_a_share_exchange(code)
    return f"{code}.{exchange.upper()}"


def split_symbol(symbol: str) -> tuple[str, str]:
    """拆成 (代码, 交易所)。"""
    if "." not in symbol:
        raise ValueError(f"缺少交易所后缀: {symbol!r}")
    code, _, exchange = symbol.rpartition(".")
    if not code or not exchange:
        raise ValueError(f"非法代码: {symbol!r}")
    return code, exchange


def is_b_share(code: str) -> bool:
    return code.strip().startswith(B_SHARE_PREFIXES)
