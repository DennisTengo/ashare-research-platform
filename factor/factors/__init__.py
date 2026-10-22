"""因子集合。

放入本目录的任意 .py 文件会被自动导入并完成注册，**不需要修改这个文件**。
这是需求 FR-5.2 的硬要求：新增因子只能新增文件，不能改任何现有代码。

单个模块导入失败不会中断整体加载——错误记在 load_errors() 里，
让界面能提示「哪个因子文件写错了」，而不是整个应用起不来。
"""

from __future__ import annotations

import importlib
import pkgutil

_LOADED: list[str] = []
_ERRORS: dict[str, str] = {}


def _discover() -> None:
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{info.name}")
            _LOADED.append(info.name)
        except Exception as exc:  # noqa: BLE001 - 单个文件出错不能拖垮全部因子
            _ERRORS[info.name] = f"{type(exc).__name__}: {exc}"


def load_errors() -> dict[str, str]:
    """加载失败的因子文件及其原因。"""
    return dict(_ERRORS)


def loaded_modules() -> list[str]:
    """成功加载的因子模块名。"""
    return list(_LOADED)


_discover()

__all__ = ["load_errors", "loaded_modules"]
