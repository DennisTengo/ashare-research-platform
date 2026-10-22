"""因子框架。

使用方式：
    import factor.factors          # 完成注册
    from factor.engine import compute_all
    compute_all(conn, ["momentum_20d"])
"""

from factor.base import Factor, all_factors, get, register

__all__ = ["Factor", "all_factors", "get", "register"]
