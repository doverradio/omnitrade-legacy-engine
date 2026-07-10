from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.core.errors import InvalidRequestError
from app.services.validation_runs import service


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _FakeDb:
    def __init__(self, *, existing_account_ids: set[uuid.UUID]):
        self.existing_account_ids = existing_account_ids
        self.added = []
        self.flushed = False
        self.committed = False

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.flushed = True
        for item in self.added:
            if item.__class__.__name__ == "ValidationRun" and getattr(item, "validation_run_id", None) is None:
                item.validation_run_id = uuid.uuid4()

    async def execute(self, statement):
        text = str(statement)
        if "FROM paper_accounts" in text:
            return _ScalarResult(self.existing_account_ids)
        return _ScalarResult([])

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_create_validation_run_persists_account_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    account_a = uuid.UUID("11111111-1111-1111-1111-111111111111")
    account_b = uuid.UUID("22222222-2222-2222-2222-222222222222")
    db = _FakeDb(existing_account_ids={account_a, account_b})

    fixed_now = datetime(2026, 7, 10, 13, 10, tzinfo=timezone.utc)

    class _FakeDateTime:
        @staticmethod
        def now(_tz=None):
            return fixed_now

    monkeypatch.setattr(service, "datetime", _FakeDateTime)

    request = service.ValidationRunCreateRequest(
        name="Run With Accounts",
        objective="Scope binding",
        duration_hours=24,
        paper_capital=Decimal("50"),
        enabled_strategies=["RSI"],
        enabled_research_agents=["Baseline"],
        enabled_research_features=["Lab"],
        paper_account_ids=[account_a, account_b],
    )

    response = await service.create_validation_run(db=db, request=request)

    assert response.validation_run_id is not None
    bindings = [item for item in db.added if item.__class__.__name__ == "ValidationRunPaperAccount"]
    assert len(bindings) == 2
    assert {item.paper_account_id for item in bindings} == {account_a, account_b}


@pytest.mark.asyncio
async def test_create_validation_run_rejects_missing_bound_account() -> None:
    existing = uuid.UUID("33333333-3333-3333-3333-333333333333")
    missing = uuid.UUID("44444444-4444-4444-4444-444444444444")
    db = _FakeDb(existing_account_ids={existing})

    request = service.ValidationRunCreateRequest(
        name="Run Missing Account",
        objective="Scope binding",
        duration_hours=24,
        paper_capital=Decimal("25"),
        enabled_strategies=["RSI"],
        enabled_research_agents=["Baseline"],
        enabled_research_features=["Lab"],
        paper_account_ids=[existing, missing],
    )

    with pytest.raises(InvalidRequestError):
        await service.create_validation_run(db=db, request=request)
