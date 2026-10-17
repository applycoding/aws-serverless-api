"""Powertools idempotency store, created on first use inside the active boto3 session."""

import os
from contextvars import ContextVar
from typing import Any

from aws_lambda_powertools.utilities.idempotency import DynamoDBPersistenceLayer

_store: DynamoDBPersistenceLayer | None = None
_lambda_context: ContextVar[Any] = ContextVar("bookings_lambda_context", default=None)


def get_persistence_store() -> DynamoDBPersistenceLayer:
    global _store
    if _store is None:
        _store = DynamoDBPersistenceLayer(table_name=os.environ["IDEMPOTENCY_TABLE_NAME"])
    return _store


def set_lambda_context(context: Any) -> None:
    """Remember the current invocation so idempotency can read the remaining time."""
    _lambda_context.set(context)


def current_lambda_context() -> Any:
    return _lambda_context.get()


def reset_persistence_store() -> None:
    """Drop the cached client. Tests call this when a new moto context starts."""
    global _store
    _store = None
    _lambda_context.set(None)
