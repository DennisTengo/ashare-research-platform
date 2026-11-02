"""成本台账的单元测试。

重点不是「成本算得对不对」这么笼统，而是四条具体行为：
    建仓只收买入费、不成交不收费、换仓两侧都收费、漂移后拉回等权也要收费。
这四条正是旧口径漏掉的地方，所以每条都要有独立断言。
"""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.costs import CostModel, drift_weights, rebalance_cost, rebalance_trade

MODEL = CostModel(commission=0.0003, stamp_tax=0.001, slippage=0.001)


def _w(**weights) -> pd.Series:
    s = pd.Series(weights, dtype=float)
    return s / s.sum()


def test_rates_split_by_side():
    """印花税只在卖出收，买入不含它。"""
    assert MODEL.buy_rate == pytest.approx(0.0013)
    assert MODEL.sell_rate == pytest.approx(0.0023)


def test_from_config_reads_defaults():
    model = CostModel.from_config()
    assert model.commission == pytest.approx(0.0003)
    assert model.stamp_tax == pytest.approx(0.001)


def test_first_period_buys_only():
    """建仓是全额买入，没有卖出，成本只按买入费率收一次。

    旧口径在这里按「换手 1.0 × (买入 + 卖出)」收了 36bp，多算了卖出费。
    """
    out = rebalance_cost(pd.Series(dtype=float), _w(A=1, B=1), MODEL)
    assert out["buy_turnover"] == pytest.approx(1.0)
    assert out["sell_turnover"] == pytest.approx(0.0)
    assert out["turnover"] == pytest.approx(1.0)
    assert out["cost"] == pytest.approx(MODEL.buy_rate)
    assert out["n_trades"] == 2


def test_no_trade_when_weights_unchanged():
    before = _w(A=1, B=1)
    out = rebalance_cost(before, before.copy(), MODEL)
    assert out["cost"] == 0.0
    assert out["n_trades"] == 0
    assert out["trades"].empty


def test_full_switch_charges_both_sides():
    before = _w(A=1, B=1)
    after = _w(C=1, D=1)
    out = rebalance_cost(before, after, MODEL)
    assert out["buy_turnover"] == pytest.approx(1.0)
    assert out["sell_turnover"] == pytest.approx(1.0)
    assert out["cost"] == pytest.approx(MODEL.buy_rate + MODEL.sell_rate)
    assert out["n_trades"] == 4


def test_partial_switch_counts_exit_too():
    """换掉一半：卖出的那只也要收费，不能只收新买的那只。"""
    out = rebalance_cost(_w(A=1, B=1), _w(A=1, C=1), MODEL)
    assert out["buy_turnover"] == pytest.approx(0.5)
    assert out["sell_turnover"] == pytest.approx(0.5)
    assert out["cost"] == pytest.approx(0.5 * (MODEL.buy_rate + MODEL.sell_rate))


def test_trade_rows_carry_side_and_cost():
    trades = rebalance_trade(_w(A=1, B=1), _w(A=1, C=1), MODEL)
    assert set(trades["side"]) == {"买入", "卖出"}
    sold = trades[trades["code"] == "B"].iloc[0]
    assert sold["side"] == "卖出"
    assert sold["delta"] == pytest.approx(-0.5)
    assert sold["cost"] == pytest.approx(0.5 * MODEL.sell_rate)


def test_drift_weights_shift_to_winner():
    """涨得多的股票，期末权重自然变大。"""
    out = drift_weights(_w(A=1, B=1), pd.Series({"A": 0.10, "B": 0.0}))
    assert out["A"] == pytest.approx(0.55 / 1.05)
    assert out.sum() == pytest.approx(1.0)


def test_drift_then_rebalance_costs_money():
    """权重没变名单也没变，但涨跌分化后拉回等权就是真实成交，必须收费。

    这是旧口径漏掉的第三项。手算：0.5 涨 10% → 0.52381，拉回 0.5，
    单边换手 0.02381。
    """
    held = _w(A=1, B=1)
    drifted = drift_weights(held, pd.Series({"A": 0.10, "B": 0.0}))
    out = rebalance_cost(drifted, held, MODEL)
    assert out["buy_turnover"] == pytest.approx(0.55 / 1.05 - 0.5)
    assert out["sell_turnover"] == pytest.approx(0.55 / 1.05 - 0.5)
    assert out["cost"] > 0


def test_missing_return_treated_as_flat_not_dropped():
    """缺收益时按不涨不跌处理，权重合计仍为 1，由调用方标注「待核算」。"""
    out = drift_weights(_w(A=1, B=1), pd.Series({"A": 0.10, "B": float("nan")}))
    assert out.sum() == pytest.approx(1.0)
    assert out["A"] == pytest.approx(0.55 / 1.05)
    assert out["B"] == pytest.approx(0.50 / 1.05)