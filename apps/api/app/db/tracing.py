"""TEMPORARY diagnostic instrumentation for the live reconciliation transaction
investigation. Logging only -- does not alter behavior, arguments, return
values, or exception propagation of anything it wraps. Remove once one
complete production trace of the InvalidRequestError has been captured.
"""
from __future__ import annotations

import functools
import logging
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.db.tracing")

_CORRELATION_ID_ATTR = "_trace_correlation_id"


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def _correlation_id(db: AsyncSession) -> str:
    return getattr(db, _CORRELATION_ID_ATTR, "unset")


def describe_transaction_state(db: AsyncSession) -> str:
    try:
        in_txn = db.in_transaction()
    except Exception as exc:  # logging must never crash the request
        in_txn = f"<error reading in_transaction(): {exc}>"
    try:
        txn = db.get_transaction()
    except Exception as exc:
        txn = f"<error reading get_transaction(): {exc}>"
    return (
        f"correlation_id={_correlation_id(db)} session_id={id(db)} "
        f"in_transaction={in_txn} transaction={txn!r}"
    )


def trace_calls(label: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Logs BEFORE/AFTER/RAISED around an async function that takes `db` as a
    keyword argument. Delegates to the original function unchanged.
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            db = kwargs.get("db")
            if db is not None:
                logger.info("[TRACE] BEFORE %s | %s", label, describe_transaction_state(db))
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                if db is not None:
                    logger.info(
                        "[TRACE] RAISED %s (%s: %s) | %s",
                        label,
                        type(exc).__name__,
                        exc,
                        describe_transaction_state(db),
                    )
                raise
            if db is not None:
                logger.info("[TRACE] AFTER  %s | %s", label, describe_transaction_state(db))
            return result

        return wrapper

    return decorator


def instrument_session_calls(db: AsyncSession) -> None:
    """Wraps scalar/scalars/execute/flush/add on THIS SESSION INSTANCE ONLY
    with before/after logging. Each wrapper calls through to the original
    bound method with the same args/kwargs and returns/raises unchanged.
    """

    def _wrap_async(name: str, original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(original)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            logger.info("[TRACE] BEFORE db.%s() | %s", name, describe_transaction_state(db))
            try:
                result = await original(*args, **kwargs)
            except Exception as exc:
                logger.info(
                    "[TRACE] RAISED db.%s() (%s: %s) | %s",
                    name,
                    type(exc).__name__,
                    exc,
                    describe_transaction_state(db),
                )
                raise
            logger.info("[TRACE] AFTER  db.%s() | %s", name, describe_transaction_state(db))
            return result

        return wrapped

    def _wrap_sync(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            logger.info("[TRACE] BEFORE db.%s() | %s", name, describe_transaction_state(db))
            try:
                result = original(*args, **kwargs)
            except Exception as exc:
                logger.info(
                    "[TRACE] RAISED db.%s() (%s: %s) | %s",
                    name,
                    type(exc).__name__,
                    exc,
                    describe_transaction_state(db),
                )
                raise
            logger.info("[TRACE] AFTER  db.%s() | %s", name, describe_transaction_state(db))
            return result

        return wrapped

    for method_name in ("scalar", "scalars", "execute", "flush"):
        setattr(db, method_name, _wrap_async(method_name, getattr(db, method_name)))
    setattr(db, "add", _wrap_sync("add", db.add))
