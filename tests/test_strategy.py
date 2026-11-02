"""策略层（选股 + 逐期模拟）的单元测试。

被测的不变量：
    选股按分位卡阈值、方向可翻；同收益同名单不产生换手；成交台账能对上
    每期成本；缺数据要标「待核算」；未走完的最后一期要剔除。
"""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.costs import CostModel
from analysis.strategy import STATUS_OK, STATUS_PENDING, TopQuantile, metrics_frame, run_strategy

MODEL = CostModel(commission=0.0003, stamp_tax=0.001, slippage=0.001)

# 十只股票，因子值 0~9，默认每期都涨 1%
VALUES = {f"A{i}": float(i) for i in range(10)}


def _day(date: str, values: dict[str, float] | None = None, rets: dict[str, float] | None = None) -> list[dict]:
    rows = []
    for code, fv in (values or VALUES).items():
        rows.append(
            {
                "trade_date": pd.Timestamp(date),
                "code": code,
                "factor_value": fv,
                "fwd_return": (rets or {}).get(code, 0.01),
            }
        )
    return rows


def test_targets_pick_top_by_default():
    day = pd.DataFrame(_day("2024-01-02"))
    # 10 只里取 2 只，最小持仓数降到 2 才能在合成样本上验证「前 20%」
    target = TopQuantile(quantile=0.2, min_holdings=2).targets(day)
    assert list(target.index) == ["A9", "A8"]
    assert target.sum() == pytest.approx(1.0)
    assert target.iloc[0] == pytest.approx(0.5)


def test_targets_pick_bottom_when_reversed():
    day = pd.DataFrame(_day("2024-01-02"))
    target = TopQuantile(quantile=0.2, descending=False, min_holdings=2).targets(day)
    assert set(target.index) == {"A0", "A1"}


def test_targets_empty_when_too_few_candidates():
    """候选不足 min_holdings 时不建仓，宁可空一期也不拿两三只股票当组合。"""
    day = pd.DataFrame(_day("2024-01-02", values={"A0": 1.0, "A1": 2.0, "A2": 3.0, "A3": 4.0}))
    assert TopQuantile(quantile=0.2).targets(day).empty


def test_targets_ignore_missing_factor_values():
    values = dict(VALUES)
    values["A9"] = float("nan")
    day = pd.DataFrame(_day("2024-01-02", values=values))
    target = TopQuantile(quantile=0.2, min_holdings=2).targets(day)
    assert "A9" not in target.index
    assert len(target) == 2


def _result(**kw):
    panel = pd.DataFrame(_day("2024-01-02") + _day("2024-02-02"))
    return run_strategy(panel, TopQuantile(quantile=0.5), MODEL, horizon=20, **kw)


def test_nav_starts_at_one_and_runs_every_period():
    result = _result()
    assert result.n_periods == 2
    assert result.nav["策略(净)"].iloc[0] == pytest.approx(1.0)
    assert set(result.metrics) == {"策略(净)", "策略(毛)", "基准(等权)", "超额(净−基准)"}


def test_identical_holdings_and_returns_produce_no_rebalance_cost():
    """名单不变、收益全相同 → 权重不漂移 → 第二期不该产生任何成交。"""
    result = _result()
    assert result.periods["cost"].iloc[0] == pytest.approx(MODEL.buy_rate)  # 建仓
    assert result.periods["cost"].iloc[1] == pytest.approx(0.0, abs=1e-12)


def test_divergent_returns_create_rebalance_turnover():
    """名单不变但涨跌不同 → 拉回等权就是成交，成本必须大于 0。

    收益要放在**第一期**：fwd_return 是持有期内的收益，期末权重就是被它
    推开的，下一期拉回等权才会产生成交。放在第二期只能影响第二期的毛利。
    """
    rets1 = {code: 0.0 for code in VALUES}
    rets1["A9"] = 0.10  # A9 在持仓里，涨幅最高
    panel = pd.DataFrame(_day("2024-01-02", rets=rets1) + _day("2024-02-02"))
    result = run_strategy(panel, TopQuantile(quantile=0.5), MODEL, horizon=20)
    assert result.periods["cost"].iloc[1] > 0
    assert result.periods["turnover"].iloc[1] > 0


def test_ledger_sums_to_period_cost():
    result = _result()
    by_date = result.ledger.groupby("trade_date")["cost"].sum()
    for date, cost in result.periods["cost"].items():
        # 没有成交的期次不会出现在台账里，按 0 比
        assert float(by_date.get(date, 0.0)) == pytest.approx(cost)


def test_missing_return_marks_period_pending():
    """持仓股缺收益时，漂移权重还原不精确，该期成本标「待核算」。"""
    rets = {"A9": float("nan")}
    panel = pd.DataFrame(_day("2024-01-02", rets=rets) + _day("2024-02-02"))
    result = run_strategy(panel, TopQuantile(quantile=0.5), MODEL, horizon=20)
    assert result.periods["cost_status"].iloc[0] == STATUS_PENDING
    assert result.periods["cost_status"].iloc[1] == STATUS_OK or result.n_pending >= 1
    assert result.n_pending >= 1


def test_incomplete_last_period_is_dropped():
    """最后一期未来收益全空说明还没走完，不能当成收益 0 参与统计。"""
    panel = pd.DataFrame(
        _day("2024-01-02")
        + _day("2024-02-02", rets={code: float("nan") for code in VALUES})
    )
    result = run_strategy(panel, TopQuantile(quantile=0.5), MODEL, horizon=20)
    assert result.n_periods == 1


def test_period_with_too_few_candidates_is_skipped():
    panel = pd.DataFrame(
        _day("2024-01-02")
        + _day("2024-02-02", values={"A0": 1.0, "A1": 2.0, "A2": 3.0, "A3": 4.0})
    )
    result = run_strategy(panel, TopQuantile(quantile=0.5), MODEL, horizon=20)
    assert result.n_skipped == 1
    assert result.n_periods == 1


def test_excess_is_net_minus_benchmark():
    result = _result()
    assert result.periods["excess"].equals(result.periods["net"] - result.periods["bench"])
    # 全池等权 1%，策略扣完成本必然低于基准
    assert result.metrics["超额(净−基准)"]["total_return"] < 0


def test_metrics_frame_is_display_ready():
    frame = metrics_frame(_result())
    assert list(frame["组合"]) == ["策略(净)", "策略(毛)", "基准(等权)", "超额(净−基准)"]
    # 数值已换算成百分比
    assert abs(frame.loc[frame["组合"] == "策略(毛)", "年化(%)"].iloc[0]) < 100


def test_empty_panel_returns_empty_result():
    result = run_strategy(pd.DataFrame(), TopQuantile(), MODEL, horizon=20)
    assert result.n_periods == 0
    assert result.ledger.empty