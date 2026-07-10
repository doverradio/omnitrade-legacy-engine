from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.core.errors import ServiceUnavailableError
from app.db import session as session_module


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
