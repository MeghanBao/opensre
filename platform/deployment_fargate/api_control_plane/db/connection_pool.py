"""Postgres connection-pool infrastructure for the control-plane database."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_POOL_MIN_CONNECTIONS = 1
_POOL_MAX_CONNECTIONS = 10


class PostgresConnectionPool:
    """Thread-safe psycopg2 connection pool wrapper."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None
        self._pool_lock = threading.Lock()

    def _get_pool(self) -> Any:
        with self._pool_lock:
            if self._pool is None:
                from psycopg2.pool import ThreadedConnectionPool

                self._pool = ThreadedConnectionPool(
                    _POOL_MIN_CONNECTIONS,
                    _POOL_MAX_CONNECTIONS,
                    self._dsn,
                )
            return self._pool

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Borrow a pooled connection and return it when the block exits."""
        pool = self._get_pool()
        connection = pool.getconn()
        try:
            with connection:
                yield connection
        finally:
            pool.putconn(connection)
