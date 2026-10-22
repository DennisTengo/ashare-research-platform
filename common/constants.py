"""跨模块共享的常量。

只放真正被多处使用的值。单个模块内部的常量留在原处，避免这里变成杂物间。
"""

from __future__ import annotations

# 一年的交易日数，用于年化换算
TRADING_DAYS_PER_YEAR = 252

# A 股默认交易成本
# 买入：佣金万三 + 滑点
# 卖出：佣金万三 + 印花税千一 + 滑点
DEFAULT_COST_BUY = 0.0013
DEFAULT_COST_SELL = 0.0023
