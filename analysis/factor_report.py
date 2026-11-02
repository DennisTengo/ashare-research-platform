"""因子快照报告：把一次完整评估固化成一条可追溯的档案。

为什么需要这一层
    因子详情页要展示代码、Simulation Settings、PNL / Sharpe / Turnover 和
    按年度的 IS Summary。如果每次打开页面都现算一遍，慢只是小问题；真正的问题是
    数字会随代码与数据漂移——同一屏上的几个指标可能来自不同版本的计算，
    看到结论却说不清它是哪天、用什么参数、哪份代码算出来的。
    做法是离线算一次，把参数、指标、年度分解、曲线一起落库，页面只读。

口径
    绩效公式一个都不在这里重写：全样本指标走 analysis.pipeline.run_backtest，
    每期收益走 analysis.backtest.portfolio_returns / long_short_returns，
    年度指标直接调 performance()。年度表与全样本用同一套公式，
    两行数字才对得上，也才解释得清。

    「年度」按调仓日所在自然年归属。20 日持有期会跨年，这只影响归属不影响数字；
    各年收益复利拼接后等于全样本累计收益，这是可以断言的不变量（见测试）。

表结构见 warehouse.schema.DDL_FACTOR_REPORT。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import textwrap
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd

from analysis.backtest import long_short_returns, performance, portfolio_returns
from analysis.grouping import TRADING_DAYS_PER_YEAR
from analysis.ic import compute_ic, ic_summary
from analysis.pipeline import build_panel, run_backtest
from common.config import load_config
from common.logger import get_logger
from factor.base import Factor

log = get_logger("analysis.factor_report")

# 落库的状态只有两种：算成了、算砸了（砸了要留原因）。
# 「未计算」「需重算」都是读的时候按指纹比对出来的，不落库——
# 一旦落库，代码改了状态却还写着「已计算」，那就是在骗人。
STATUS_READY = "已计算"
STATUS_FAILED = "计算失败"

# 界面上额外出现的两种派生状态
STATUS_MISSING = "未计算"
STATUS_STALE = "需重算"

TABLE = "meta_factor_report"
_COLUMNS = (
    "factor_name", "params_hash", "status", "code_hash", "params",
    "metrics", "yearly", "curve", "n_periods", "computed_at", "error",
)


def source_code(factor: Factor) -> str:
    """因子函数源码。详情页要展示它，code_hash 也由它算出来。"""
    try:
        return textwrap.dedent(inspect.getsource(factor.fn)).strip()
    except (OSError, TypeError):
        # 交互式定义（如测试里动态构造）拿不到源码，退化成空串而不是崩掉
        return ""


def code_hash(factor: Factor) -> str:
    """影响数字的那部分声明的指纹：函数源码 + lookback + requires。

    不含 description：改一句人话说明不该触发重算。
    horizon / groups 已经进了 params_hash，不在这里重复。
    """
    raw = "\n".join(
        [source_code(factor), f"lookback={factor.lookback}", f"requires={','.join(factor.requires)}"]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def compute_params(
    factor: Factor,
    start: str | None = None,
    end: str | None = None,
    min_listed: int = 60,
) -> dict:
    """这次评估用的全部设置。既是 Simulation Settings 的展示内容，也是 params_hash 的输入。

    成本取自配置文件（FR-7.1：成本参数统一在配置文件里），
    不在代码里另写一套默认值。
    """
    bt = load_config().backtest
    return {
        "horizon": factor.horizon,
        "groups": factor.groups,
        "min_listed": min_listed,
        "commission": bt.commission_rate,
        "stamp_tax": bt.stamp_tax,
        "slippage": bt.slippage,
        "cost_buy": round(bt.commission_rate + bt.slippage, 6),
        "cost_sell": round(bt.commission_rate + bt.stamp_tax + bt.slippage, 6),
        "start": start,
        "end": end,
        # 下面三条不是参数，是当前实现的假设，必须和数字一起展示——
        # 否则「为什么和券商软件对不上」会变成说不清的事。
        "universe": "沪深 300 成分股（当前快照，存在幸存者偏差）",
        "trade_price": "T+1 收盘（FR-6.2 的开盘成交尚未实现）",
        "adjust": "后复权价（close × adj_factor）",
        "benchmark": "池内等权",
    }


def params_hash(params: dict) -> str:
    """参数指纹。字典顺序不影响结果，否则每次重跑都会生成新档案。"""
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _clean(value):
    """转成严格 JSON：numpy 标量转 float、NaN/Inf 转 null。

    不转的话 json.dumps 会写出 NaN，那不是合法 JSON，读回来会炸。
    """
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, 8)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if value is None or isinstance(value, (int, str, bool)):
        return value
    return str(value)


def _dumps(value) -> str:
    return json.dumps(_clean(value), ensure_ascii=False, allow_nan=False)


def _cumulative(returns: pd.Series) -> list[float]:
    """每期收益 → 累计净值（首期为 1+x，不额外补 1.0 的点）。"""
    clean = returns.dropna()
    return [round(float(v), 6) for v in (1.0 + clean).cumprod()]


def _yearly_performance(returns: pd.Series, ppy: int) -> list[dict]:
    """按调仓日所在年切分，每年单独算一遍绩效。

    每期收益独立（非重叠调仓），所以直接按年切片即可，不需要拼接净值。
    """
    rows = []
    clean = returns.dropna()
    for year, chunk in clean.groupby(clean.index.year):
        perf = performance(chunk, ppy)
        rows.append({"year": int(year), **{k: v for k, v in perf.items() if k != "label"}})
    return rows


def _yearly_ic(ic: pd.Series, ppy: int) -> list[dict]:
    rows = []
    clean = ic.dropna()
    for year, chunk in clean.groupby(clean.index.year):
        stats = ic_summary(chunk, ppy)
        rows.append(
            {
                "year": int(year),
                "periods": stats["periods"],
                "ic_mean": stats["ic_mean"],
                "ic_std": stats["ic_std"],
                "icir": stats.get("icir_annual", stats["icir"]),
                "win_rate": stats["win_rate"],
            }
        )
    return rows


def _rolling_sharpe(returns: pd.Series, ppy: int) -> list[float | None]:
    """滚动夏普，窗口取一年（ppy 期）。前几期为 null，图上留空而不是画假值。"""
    window = max(2, ppy)
    mean = returns.rolling(window).mean()
    std = returns.rolling(window).std()
    sharpe = mean / std * np.sqrt(ppy)
    return [None if pd.isna(v) else round(float(v), 3) for v in sharpe]


def build_report(
    conn: duckdb.DuckDBPyConnection,
    factor: Factor,
    start: str | None = None,
    end: str | None = None,
    min_listed: int = 60,
) -> dict:
    """跑一次完整评估，返回可落库的档案字典。算不出样本时抛 ValueError。"""
    params = compute_params(factor, start, end, min_listed)
    ph = params_hash(params)
    ppy = max(1, round(TRADING_DAYS_PER_YEAR / factor.horizon))

    # 分组面板：曲线、IC、年度分解都从这一份面板派生
    panel = build_panel(
        conn, factor.name, horizon=factor.horizon, groups=factor.groups,
        min_listed=min_listed, start=start, end=end,
    )
    if panel.empty or panel.dropna(subset=["fwd_return"]).empty:
        raise ValueError(f"因子 {factor.name} 在区间内没有可回测的样本")

    cost_buy, cost_sell = params["cost_buy"], params["cost_sell"]
    bench = portfolio_returns(panel, None, 0.0, 0.0)
    top = portfolio_returns(panel, factor.groups, cost_buy, cost_sell)
    spread = long_short_returns(panel, factor.groups, cost_buy, cost_sell)
    ic = compute_ic(panel)

    # 全样本指标直接取 run_backtest 的结果：列表页、CLI、存档必须是同一套数字。
    # 代价是面板会再构建一次（约 2 秒），换来「口径只有一个出口」。
    result = run_backtest(
        conn, factor.name, horizon=factor.horizon, groups=factor.groups,
        min_listed=min_listed, cost_buy=cost_buy, cost_sell=cost_sell,
        start=start, end=end,
    )

    dates = sorted(set(spread.index) & set(top.index) & set(bench.index))
    # 期数取「实际算到收益的调仓日」，不取面板里的日期数：
    # 最后一个调仓日的未来收益还没走完，会被 portfolio_returns 滤掉，
    # 若按面板数目报期数，列表页会写着 80 而图上只有 79 个点。
    n_periods = len(dates)
    returns = {
        "多空(净)": spread["net"].reindex(dates),
        "多头(净)": top["net"].reindex(dates),
        "基准(等权)": bench["net"].reindex(dates),
    }
    ic_aligned = ic.reindex(dates)

    curve = {
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "pnl": {label: _cumulative(series) for label, series in returns.items()},
        "turnover": [
            None if pd.isna(v) else round(float(v), 4) for v in top["turnover"].reindex(dates)
        ],
        "ic": [None if pd.isna(v) else round(float(v), 5) for v in ic_aligned],
        "rolling_sharpe": _rolling_sharpe(spread["net"].reindex(dates), ppy),
    }

    yearly = {label: _yearly_performance(series, ppy) for label, series in returns.items()}
    yearly["IC"] = _yearly_ic(ic_aligned, ppy)

    return {
        "factor_name": factor.name,
        "params_hash": ph,
        "status": STATUS_READY,
        "code_hash": code_hash(factor),
        "params": params,
        "metrics": {
            "n_periods": n_periods,
            "portfolios": result.groups.to_dict(orient="records"),
            "ic": ic_summary(ic_aligned, ppy),
            "first_date": dates[0].strftime("%Y-%m-%d") if dates else None,
            "last_date": dates[-1].strftime("%Y-%m-%d") if dates else None,
        },
        "yearly": yearly,
        "curve": curve,
        "n_periods": n_periods,
        "computed_at": datetime.now(),
        "error": None,
    }


def save_report(conn: duckdb.DuckDBPyConnection, report: dict) -> None:
    """写入 / 覆盖一条档案。同一 (因子, 参数指纹) 只保留最新一份。"""
    placeholders = ", ".join("?" for _ in _COLUMNS)
    conn.execute(
        f"INSERT OR REPLACE INTO {TABLE} ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
        [
            report["factor_name"],
            report["params_hash"],
            report["status"],
            report["code_hash"],
            _dumps(report["params"]),
            _dumps(report["metrics"]),
            _dumps(report["yearly"]),
            _dumps(report["curve"]),
            report["n_periods"],
            report["computed_at"],
            report["error"],
        ],
    )


def record_failure(
    conn: duckdb.DuckDBPyConnection,
    factor: Factor,
    message: str,
    start: str | None = None,
    end: str | None = None,
    min_listed: int = 60,
) -> None:
    """算不出来也要留痕：页面必须能说出「为什么没有数字」。"""
    params = compute_params(factor, start, end, min_listed)
    conn.execute(
        f"INSERT OR REPLACE INTO {TABLE} "
        "(factor_name, params_hash, status, code_hash, params, n_periods, computed_at, error) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        [
            factor.name,
            params_hash(params),
            STATUS_FAILED,
            code_hash(factor),
            _dumps(params),
            datetime.now(),
            message[:1000],
        ],
    )


def _decode(row: tuple) -> dict:
    keys = ("factor_name", "params_hash", "status", "code_hash", "params",
            "metrics", "yearly", "curve", "n_periods", "computed_at", "error")
    out = dict(zip(keys, row))
    for col in ("params", "metrics", "yearly", "curve"):
        out[col] = json.loads(out[col]) if out[col] else None
    return out


def load_reports(conn: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    """每个因子最新的一份档案，{因子名: 档案}。

    同一因子可能有历史参数的多份档案，列表只认最新的那份；
    要看旧的按 params_hash 单独取。
    """
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM {TABLE} ORDER BY computed_at DESC NULLS LAST"
    ).fetchall()
    latest: dict[str, dict] = {}
    for row in rows:
        record = _decode(row)
        latest.setdefault(record["factor_name"], record)
    return latest


def load_report(conn: duckdb.DuckDBPyConnection, factor_name: str) -> dict | None:
    return load_reports(conn).get(factor_name)


def status_of(factor: Factor, record: dict | None, params: dict) -> str:
    """按指纹比对给出界面状态。不落库，纯推导。"""
    if record is None:
        return STATUS_MISSING
    if record["status"] == STATUS_FAILED:
        return STATUS_FAILED
    if record["params_hash"] != params_hash(params) or record["code_hash"] != code_hash(factor):
        return STATUS_STALE
    return STATUS_READY