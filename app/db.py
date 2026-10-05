"""PostgreSQL 连接池管理。"""

from __future__ import annotations

import os
import time

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DEFAULT_DSN = "postgresql://handover:handover@localhost:5432/handover"

_pool: ConnectionPool | None = None


def init_pool(retries: int = 10, delay: float = 2.0) -> ConnectionPool:
    """初始化连接池并等待数据库就绪（带重试，便于容器编排启动）。"""
    global _pool
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    _pool = ConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=8,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            _pool.open()
            _pool.wait(timeout=30)
            return _pool
        except Exception as exc:  # noqa: BLE001 - 启动阶段等待 DB 就绪
            last_exc = exc
            time.sleep(delay)
    raise RuntimeError(f"数据库连接失败: {last_exc}")


def get_pool() -> ConnectionPool:
    assert _pool is not None, "连接池尚未初始化"
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
