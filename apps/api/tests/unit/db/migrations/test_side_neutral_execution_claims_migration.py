from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import Mock, call

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from tests.support.real_sqlite_session import real_sqlite_session


_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "app/db/migrations/versions/20260727_0054_side_neutral_execution_claims.py"
)


def _load_migration(*, operation: Mock, monkeypatch: pytest.MonkeyPatch):
    # Migration structure should remain testable under the repository's
    # lightweight system-pytest environment, where Alembic itself is only
    # installed in the application venv used for SQL smoke tests.
    monkeypatch.setitem(sys.modules, "alembic", types.SimpleNamespace(op=operation))
    spec = importlib.util.spec_from_file_location("side_neutral_execution_claims_0054", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_and_downgrade_are_exact_reversible_constraint_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    operation = Mock()
    migration = _load_migration(operation=operation, monkeypatch=monkeypatch)

    migration.upgrade()
    assert operation.mock_calls == [
        call.drop_constraint("ck_aec_buy_only", "autonomous_execution_claims", type_="check"),
        call.create_check_constraint(
            "ck_aec_side", "autonomous_execution_claims", "side IN ('BUY','SELL')",
        ),
    ]

    operation.reset_mock()
    migration.downgrade()
    assert operation.mock_calls == [
        call.drop_constraint("ck_aec_side", "autonomous_execution_claims", type_="check"),
        call.create_check_constraint("ck_aec_buy_only", "autonomous_execution_claims", "side = 'BUY'"),
    ]


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session([AutonomousExecutionClaim.__table__]) as session:
        yield session


def _claim(*, side: str) -> AutonomousExecutionClaim:
    return AutonomousExecutionClaim(
        claim_id=uuid.uuid4(), package_id=uuid.uuid4(), activation_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(), campaign_version=1, mandate_id=uuid.uuid4(),
        mandate_version_id=uuid.uuid4(), account_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        connection_id=uuid.uuid4(), provider="kraken_spot", environment="production",
        product="BTC-USD", side=side, claim_status="CLAIMED",
        claimed_at=datetime.now(timezone.utc), claim_owner="test:migration", attempt_count=1,
    )


@pytest.mark.asyncio
async def test_database_constraint_accepts_buy_and_sell_and_rejects_invalid_side() -> None:
    async with _session() as session:
        session.add(_claim(side="BUY"))
        await session.commit()
        session.add(_claim(side="SELL"))
        await session.commit()

        session.add(_claim(side="HOLD"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
