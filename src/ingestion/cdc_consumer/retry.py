"""Bounded retry policy for external CDC side effects."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from common.storage import ImmutableCollisionError, StorageBackendError

T = TypeVar("T")


class RetryExhaustedError(RuntimeError):
    """Raised after all bounded attempts for a retryable operation fail."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    initial_backoff_seconds: float
    maximum_backoff_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.initial_backoff_seconds <= 0:
            raise ValueError("initial_backoff_seconds must be greater than zero")
        if self.maximum_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("maximum_backoff_seconds must not be below initial backoff")


def is_retryable(error: BaseException) -> bool:
    """Classify transient local/network errors; collisions are never retried."""

    if isinstance(error, ImmutableCollisionError):
        return False
    # The shared MinIO backend already classifies S3/HTTP failures and performs
    # its own bounded retries. A surfaced storage error is exhausted or terminal.
    if isinstance(error, StorageBackendError):
        return False
    return isinstance(
        error,
        (
            sqlite3.OperationalError,
            TimeoutError,
            ConnectionError,
            OSError,
        ),
    )


def retry_call(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy,
    retryable: Callable[[BaseException], bool] = is_retryable,
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    delay = policy.initial_backoff_seconds
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if not retryable(exc):
                raise
            if attempt == policy.max_attempts:
                raise RetryExhaustedError(
                    f"Operation failed after {attempt} bounded attempts"
                ) from exc
            sleeper(delay)
            delay = min(delay * 2, policy.maximum_backoff_seconds)
    raise AssertionError("Unreachable retry state")
