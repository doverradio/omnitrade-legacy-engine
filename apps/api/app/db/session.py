from collections.abc import AsyncGenerator
from collections.abc import Awaitable, Callable
import logging

from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import ServiceUnavailableError
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,
    pool_recycle=settings.database_pool_recycle_seconds,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
    connect_args={
        "timeout": settings.database_connect_timeout_seconds,
        "command_timeout": settings.database_command_timeout_seconds,
    },
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_RETRYABLE_DB_ERROR_SUBSTRINGS = (
    "connection is closed",
    "connection not open",
    "server closed the connection unexpectedly",
    "closed the connection unexpectedly",
    "terminating connection",
    "connection was closed",
)


def is_retryable_db_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, (DBAPIError, InterfaceError)) and getattr(exc, "connection_invalidated", False):
        return True

    current: BaseException | None = exc
    while current is not None:
        message = str(current).lower()
        if any(fragment in message for fragment in _RETRYABLE_DB_ERROR_SUBSTRINGS):
            return True
        current = current.__cause__
    return False


async def dispose_database_engine() -> None:
    await engine.dispose()


async def _invalidate_session(session: AsyncSession) -> None:
    try:
        connection = await session.connection()
    except Exception:
        connection = None

    if connection is not None:
        try:
            await connection.invalidate()
        except Exception:
            logger.debug("Unable to invalidate async session connection", exc_info=True)

    try:
        await session.rollback()
    except Exception:
        logger.debug("Unable to rollback failed async session", exc_info=True)


async def run_read_with_retry(
    operation: Callable[[AsyncSession], Awaitable[AsyncSession | object]],
    *,
    operation_name: str,
):
    first_error: BaseException | None = None

    for attempt in range(2):
        async with AsyncSessionLocal() as session:
            try:
                return await operation(session)
            except Exception as exc:
                retryable = is_retryable_db_connection_error(exc)
                if not retryable or attempt == 1:
                    if retryable:
                        logger.warning(
                            "database_read_retry_failed operation=%s attempt=%s",
                            operation_name,
                            attempt + 1,
                            exc_info=True,
                        )
                        raise ServiceUnavailableError(
                            message="Database temporarily unavailable",
                            details={"operation": operation_name},
                        ) from (first_error or exc)
                    raise

                first_error = exc
                logger.warning(
                    "database_read_retry_attempt operation=%s attempt=%s",
                    operation_name,
                    attempt + 1,
                    exc_info=True,
                )
                await _invalidate_session(session)
                await dispose_database_engine()

    raise ServiceUnavailableError(message="Database temporarily unavailable", details={"operation": operation_name})


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await _invalidate_session(session)
            raise
