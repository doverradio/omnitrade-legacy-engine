from __future__ import annotations

from collections.abc import Callable
import importlib
from typing import Any
from types import SimpleNamespace

import pytest

from app.core.errors import ServiceUnavailableError
import app.config as config_module
from app.db import session as session_module
import sqlalchemy.ext.asyncio as sa_async


class _FakeConnection:
    def __init__(self) -> None:
        self.invalidated = False

    async def invalidate(self) -> None:
        self.invalidated = True


class _FakeSession:
    def __init__(self) -> None:
        self.connection_obj = _FakeConnection()
        self.rollback_calls = 0
        self.close_calls = 0

    async def connection(self) -> _FakeConnection:
        return self.connection_obj

    async def rollback(self) -> None:
        self.rollback_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class _SessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.session.close()


class _SessionFactory:
    def __init__(self, sessions: list[_FakeSession]) -> None:
        self.sessions = sessions
        self.index = 0

    def __call__(self) -> _SessionContext:
        session = self.sessions[self.index]
        self.index += 1
        return _SessionContext(session)


@pytest.mark.asyncio
async def test_run_read_with_retry_retries_once_for_closed_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [_FakeSession(), _FakeSession()]
    factory = _SessionFactory(sessions)
    dispose_calls = {"count": 0}
    attempts = {"count": 0}

    async def _dispose() -> None:
        dispose_calls["count"] += 1

    async def _operation(_session: _FakeSession) -> object:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("connection is closed")
        return {"ok": True}

    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(session_module, "dispose_database_engine", _dispose)

    result = await session_module.run_read_with_retry(_operation, operation_name="test_read")

    assert result == {"ok": True}
    assert attempts["count"] == 2
    assert sessions[0].rollback_calls == 1
    assert sessions[0].connection_obj.invalidated is True
    assert dispose_calls["count"] == 1


@pytest.mark.asyncio
async def test_run_read_with_retry_raises_service_unavailable_after_second_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [_FakeSession(), _FakeSession()]
    factory = _SessionFactory(sessions)
    dispose_calls = {"count": 0}

    async def _dispose() -> None:
        dispose_calls["count"] += 1

    async def _operation(_session: _FakeSession) -> object:
        raise RuntimeError("connection is closed")

    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(session_module, "dispose_database_engine", _dispose)

    with pytest.raises(ServiceUnavailableError, match="Database temporarily unavailable"):
        await session_module.run_read_with_retry(_operation, operation_name="test_read")

    assert sessions[0].rollback_calls == 1
    assert sessions[0].connection_obj.invalidated is True
    assert dispose_calls["count"] == 1


@pytest.mark.asyncio
async def test_run_read_with_retry_does_not_retry_non_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [_FakeSession()]
    factory = _SessionFactory(sessions)
    attempts = {"count": 0}

    async def _operation(_session: _FakeSession) -> object:
        attempts["count"] += 1
        raise ValueError("boom")

    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)

    with pytest.raises(ValueError, match="boom"):
        await session_module.run_read_with_retry(_operation, operation_name="test_read")

    assert attempts["count"] == 1
    assert sessions[0].rollback_calls == 0


def test_engine_uses_bounded_asyncpg_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeEngine:
        async def dispose(self) -> None:
            return None

    fake_settings = SimpleNamespace(
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/omnitrade",
        database_pool_recycle_seconds=1800,
        database_pool_size=10,
        database_max_overflow=20,
        database_pool_timeout_seconds=30,
        database_connect_timeout_seconds=5,
        database_command_timeout_seconds=10,
    )

    def _fake_create_async_engine(url: str, **kwargs: Any) -> _FakeEngine:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeEngine()

    monkeypatch.setattr(config_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(sa_async, "create_async_engine", _fake_create_async_engine)
    monkeypatch.setattr(sa_async, "async_sessionmaker", lambda *args, **kwargs: "SESSION_FACTORY")

    reloaded = importlib.reload(session_module)

    assert captured["url"] == fake_settings.database_url
    assert captured["kwargs"]["pool_pre_ping"] is True
    assert captured["kwargs"]["pool_timeout"] == fake_settings.database_pool_timeout_seconds
    assert captured["kwargs"]["connect_args"]["timeout"] == fake_settings.database_connect_timeout_seconds
    assert captured["kwargs"]["connect_args"]["command_timeout"] == fake_settings.database_command_timeout_seconds
    assert reloaded.AsyncSessionLocal == "SESSION_FACTORY"

    monkeypatch.undo()
    importlib.reload(session_module)
