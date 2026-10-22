"""因子集合。

导入本包即完成全部因子的注册——模块顶层的 register() 调用是注册的唯一入口。
新增因子时，在这里加一行导入即可，不需要改任何其他文件。
"""

from factor.factors import momentum, reversal, volatility  # noqa: F401

__all__ = ["momentum", "reversal", "volatility"]
