"""代码标准化的测试。"""

from __future__ import annotations

import pytest

from common.symbols import (
    infer_a_share_exchange,
    is_b_share,
    split_symbol,
    to_symbol,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("600000", "SH"),
        ("601318", "SH"),
        ("688981", "SH"),
        ("000001", "SZ"),
        ("002594", "SZ"),
        ("300750", "SZ"),
        ("302132", "SZ"),
        ("430047", "BJ"),
        ("830799", "BJ"),
        ("920002", "BJ"),
    ],
)
def test_infer_exchange(code, expected):
    assert infer_a_share_exchange(code) == expected


def test_to_symbol_a_share():
    assert to_symbol("600000") == "600000.SH"
    assert to_symbol("000001") == "000001.SZ"


def test_to_symbol_with_explicit_exchange_supports_other_markets():
    """非 A 股市场显式传交易所，验证扩展性。"""
    assert to_symbol("AAPL", "US") == "AAPL.US"
    assert to_symbol("00700", "HK") == "00700.HK"


def test_split_symbol():
    assert split_symbol("600000.SH") == ("600000", "SH")
    assert split_symbol("AAPL.US") == ("AAPL", "US")


@pytest.mark.parametrize("bad", ["60000", "1234567", "abcdef", ""])
def test_invalid_code_raises(bad):
    with pytest.raises(ValueError):
        infer_a_share_exchange(bad)


def test_split_symbol_without_suffix_raises():
    with pytest.raises(ValueError, match="后缀"):
        split_symbol("600000")


def test_is_b_share():
    assert is_b_share("900901")
    assert is_b_share("200002")
    assert not is_b_share("600000")
