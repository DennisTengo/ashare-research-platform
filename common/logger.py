"""日志。

统一格式与落盘规则：控制台彩色输出，同时按天切分写入 logs/。
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{line} | {message}"


def setup_logger(
    log_dir: str | Path = "logs",
    level: str = "INFO",
    name: str = "app",
    console: bool = True,
) -> "logger.__class__":
    """初始化全局日志器，返回 logger 供直接使用。"""
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)

    logger.remove()
    if console:
        logger.add(sys.stderr, level=level, format=FORMAT, colorize=True)
    logger.add(
        directory / f"{name}_{{time:YYYY-MM-DD}}.log",
        level=level,
        format=FORMAT,
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
    )
    return logger


def get_logger(module: str):
    """按模块取 logger，便于过滤。"""
    return logger.bind(module=module)
