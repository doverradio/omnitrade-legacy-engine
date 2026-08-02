from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.core.errors import InvalidRequestError
from app.schemas.capital_campaign_domain import (
    CampaignCompoundingPolicy,
    CampaignProfitDistributionPolicy,
    CapitalCampaignDraftCreateRequest,
    CapitalCampaignPreviewRequest,
)
from app.services.capital_campaign_domain.service import create_campaign_draft, preview_campaign_definition


class _FakeDb:
    def __init__(self) -> None:
        self.commit_calls = 0
        self._runtime_campaigns = []

    def add(self, obj) -> None:
        self._runtime_campaigns.append(obj)

    async def flush(self) -> None:
        return None

    async def scalar(self, _statement):
        return None

    async def commit(self) -> None:
        self.commit_calls += 1


class _FakeRepository:
    def __init__(self, _db) -> None:
        self._store = {}

    async def next_version(self, *, campaign_id):
        existing = [key[1] for key in self._store if key[0] == campaign_id]
        return max(existing, default=0) + 1

    async def create(self, definition):
        key = (definition.campaign_id, definition.version)
        self._store[key] = definition
        return definition

    async def get(self, *, campaign_id, version=None):
        candidates = [value for (cid, _), value in self._store.items() if cid == campaign_id]
        if not candidates:
            return None
        if version is None:
            return sorted(candidates, key=lambda item: item.version, reverse=True)[0]
        for candidate in candidates:
            if candidate.version == version:
                return candidate
        return None


@pytest.mark.asyncio
async def test_valid_draft_campaign_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    fake_repo = _FakeRepository(db)

    monkeypatch.setattr("app.services.capital_campaign_domain.service.CapitalCampaignDomainRepository", lambda _db: fake_repo)

    result = await create_campaign_draft(
        db=db,
        request=CapitalCampaignDraftCreateRequest(
            name="Max Governed Campaign",
            description="non-live preview",
            owner_identity="operator",
            status="DRAFT",
            capital_budget=Decimal("25"),
            base_currency="USD",
            allowed_asset_classes=["crypto"],
            allowed_venues=["kraken_spot"],
            allowed_instruments=["BTC-USD", "ETH-USD", "SOL-USD"],
            campaign_modes=["OPPORTUNITY_SEEKING"],
            maximum_open_positions=2,
            maximum_position_size=Decimal("10"),
            minimum_position_size=Decimal("2"),
            maximum_total_exposure=Decimal("20"),
            profitability_policy_id="pfp-1.1",
            profitability_policy_version="1.0.0",
            risk_policy_id="risk-v1",
            risk_policy_version="1.0.0",
            compounding_policy=CampaignCompoundingPolicy(
                policy_type="REINVEST_PERCENTAGE",
                reinvestment_percentage=Decimal("50"),
                profit_distribution_percentage=Decimal("30"),
                reserve_percentage=Decimal("20"),
                cumulative_profit_target=Decimal("20"),
                maximum_campaign_loss=Decimal("5"),
                campaign_end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
            ),
            profit_distribution_policy=CampaignProfitDistributionPolicy(
                reinvestment_percentage=Decimal("50"),
                profit_distribution_percentage=Decimal("30"),
                reserve_percentage=Decimal("20"),
            ),
            aggression_mode="BALANCED",
            non_live_only=True,
        ),
    )

    assert result.version == 1
    assert result.status == "DRAFT"
    assert result.runtime_campaign_uuid == result.campaign_id
    assert result.runtime_definition_version == 1
    assert db.commit_calls == 1


@pytest.mark.asyncio
async def test_versioned_campaign_definitions_increment(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    fake_repo = _FakeRepository(db)
    monkeypatch.setattr("app.services.capital_campaign_domain.service.CapitalCampaignDomainRepository", lambda _db: fake_repo)

    campaign_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    first = await create_campaign_draft(
        db=db,
        request=CapitalCampaignDraftCreateRequest(
            campaign_id=campaign_id,
            name="Campaign V1",
            owner_identity="operator",
            status="DRAFT",
            capital_budget=Decimal("25"),
            base_currency="USD",
            allowed_asset_classes=["crypto"],
            allowed_venues=["kraken_spot"],
            allowed_instruments=["BTC-USD"],
            campaign_modes=["OPPORTUNITY_SEEKING"],
            maximum_open_positions=1,
            maximum_position_size=Decimal("10"),
            minimum_position_size=Decimal("2"),
            maximum_total_exposure=Decimal("10"),
            profitability_policy_id="pfp-1.1",
            profitability_policy_version="1.0.0",
            risk_policy_id="risk-v1",
            risk_policy_version="1.0.0",
            compounding_policy=CampaignCompoundingPolicy(
                policy_type="REINVEST_PERCENTAGE",
                reinvestment_percentage=Decimal("50"),
                profit_distribution_percentage=Decimal("30"),
                reserve_percentage=Decimal("20"),
                cumulative_profit_target=Decimal("20"),
                maximum_campaign_loss=Decimal("5"),
                campaign_end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
            ),
            profit_distribution_policy=CampaignProfitDistributionPolicy(
                reinvestment_percentage=Decimal("50"),
                profit_distribution_percentage=Decimal("30"),
                reserve_percentage=Decimal("20"),
            ),
            non_live_only=True,
        ),
    )

    second = await create_campaign_draft(
        db=db,
        request=CapitalCampaignDraftCreateRequest(
            campaign_id=campaign_id,
            name="Campaign V2",
            owner_identity="operator",
            status="DRAFT",
            capital_budget=Decimal("25"),
            base_currency="USD",
            allowed_asset_classes=["crypto"],
            allowed_venues=["kraken_spot"],
            allowed_instruments=["BTC-USD"],
            campaign_modes=["OPPORTUNITY_SEEKING"],
            maximum_open_positions=1,
            maximum_position_size=Decimal("10"),
            minimum_position_size=Decimal("2"),
            maximum_total_exposure=Decimal("10"),
            profitability_policy_id="pfp-1.1",
            profitability_policy_version="1.0.0",
            risk_policy_id="risk-v1",
            risk_policy_version="1.0.0",
            compounding_policy=CampaignCompoundingPolicy(
                policy_type="REINVEST_PERCENTAGE",
                reinvestment_percentage=Decimal("50"),
                profit_distribution_percentage=Decimal("30"),
                reserve_percentage=Decimal("20"),
                cumulative_profit_target=Decimal("20"),
                maximum_campaign_loss=Decimal("5"),
                campaign_end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
            ),
            profit_distribution_policy=CampaignProfitDistributionPolicy(
                reinvestment_percentage=Decimal("50"),
                profit_distribution_percentage=Decimal("30"),
                reserve_percentage=Decimal("20"),
            ),
            non_live_only=True,
        ),
    )

    assert first.version == 1
    assert second.version == 2


@pytest.mark.asyncio
async def test_unsupported_instrument_rejected_on_draft_create(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    fake_repo = _FakeRepository(db)
    monkeypatch.setattr("app.services.capital_campaign_domain.service.CapitalCampaignDomainRepository", lambda _db: fake_repo)

    with pytest.raises(InvalidRequestError):
        await create_campaign_draft(
            db=db,
            request=CapitalCampaignDraftCreateRequest(
                name="Invalid Instruments",
                owner_identity="operator",
                status="DRAFT",
                capital_budget=Decimal("25"),
                base_currency="USD",
                allowed_asset_classes=["crypto"],
                allowed_venues=["kraken_spot"],
                allowed_instruments=["DOGE-USD"],
                campaign_modes=["OPPORTUNITY_SEEKING"],
                maximum_open_positions=1,
                maximum_position_size=Decimal("10"),
                minimum_position_size=Decimal("2"),
                maximum_total_exposure=Decimal("10"),
                profitability_policy_id="pfp-1.1",
                profitability_policy_version="1.0.0",
                risk_policy_id="risk-v1",
                risk_policy_version="1.0.0",
                compounding_policy=CampaignCompoundingPolicy(
                    policy_type="REINVEST_PERCENTAGE",
                    reinvestment_percentage=Decimal("50"),
                    profit_distribution_percentage=Decimal("30"),
                    reserve_percentage=Decimal("20"),
                    cumulative_profit_target=Decimal("20"),
                    maximum_campaign_loss=Decimal("5"),
                    campaign_end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
                ),
                profit_distribution_policy=CampaignProfitDistributionPolicy(
                    reinvestment_percentage=Decimal("50"),
                    profit_distribution_percentage=Decimal("30"),
                    reserve_percentage=Decimal("20"),
                ),
                non_live_only=True,
            ),
        )


def test_no_execution_side_effect_imports() -> None:
    root = Path(__file__).resolve().parents[4] / "app" / "services" / "capital_campaign_domain"
    source = "\n".join((root / name).read_text() for name in ["service.py", "preview_engine.py", "repository.py"])
    normalized = source.lower()

    assert "create_order" not in normalized
    assert "submit_order" not in normalized
    assert "addorder" not in normalized


def test_no_provider_order_calls() -> None:
    root = Path(__file__).resolve().parents[4] / "app" / "services" / "capital_campaign_domain"
    source = "\n".join((root / name).read_text() for name in ["service.py", "preview_engine.py", "repository.py"])
    normalized = source.lower()

    assert "exchange_connections.providers" not in normalized
    assert "kraken_spot" not in normalized


# --- list_campaign_definitions: governing resolution while a DRAFT successor exists ---
#
# Confirmed production defect: with latest_only=True and no status filter,
# this function fetched only the highest version-NUMBER row per campaign_id
# (repository.list(latest_only=True)), then checked whether THAT row
# happened to be the runtime's governing pin -- dropping the campaign_id
# from the listing entirely whenever it wasn't, which is exactly what
# happens the moment an unpromoted DRAFT successor (a higher version number
# than the actual governing predecessor) exists. Real callers of this exact
# argument combination: run_campaign_orchestration_preview_for_candle (every
# candle close) and the operator unattended-eligibility-audit CLI command --
# both went blind to the still-governing version the instant a successor
# was created. Real AsyncSession/aiosqlite session, not a mock, so the real
# SQL (including repository.list's max-version-per-campaign_id subquery)
# runs for real.


def _real_campaign_session():
    from app.models.capital_campaign import CapitalCampaign
    from app.models.capital_campaign_definition import CapitalCampaignDefinition
    from tests.support.real_sqlite_session import real_sqlite_session

    return real_sqlite_session([CapitalCampaignDefinition.__table__, CapitalCampaign.__table__])


async def _seed_definition(session, *, campaign_id: UUID, version: int, status: str, allowed_instruments: list[str]) -> None:  # noqa: ANN001
    from app.models.capital_campaign_definition import CapitalCampaignDefinition

    session.add(
        CapitalCampaignDefinition(
            campaign_id=campaign_id, version=version, name="test", owner_identity="operator:test", status=status,
            capital_budget=Decimal("25"), remaining_unallocated_capital=Decimal("25"), base_currency="USD",
            allowed_asset_classes=["crypto"], allowed_venues=["kraken_spot"], allowed_instruments=allowed_instruments,
            campaign_modes=[], maximum_open_positions=1, maximum_position_size=Decimal("5"),
            minimum_position_size=Decimal("1"), maximum_total_exposure=Decimal("5"),
            profitability_policy_id="p", profitability_policy_version="1", risk_policy_id="r", risk_policy_version="1",
            compounding_policy={"policy_type": "FIXED_CAPITAL"},
        )
    )


async def _seed_runtime(session, *, campaign_id: UUID, definition_version: int, status: str = "READY") -> None:  # noqa: ANN001
    from app.models.capital_campaign import CapitalCampaign

    session.add(
        CapitalCampaign(
            uuid=campaign_id, owner="operator:test", name="test", status=status, campaign_type="definition_pinned_runtime",
            definition_campaign_id=campaign_id, definition_version=definition_version,
            starting_capital=Decimal("25"), current_equity=Decimal("25"),
        )
    )


@pytest.mark.asyncio
async def test_list_campaign_definitions_resolves_governing_version_while_draft_successor_exists() -> None:
    from app.services.capital_campaign_domain.service import list_campaign_definitions

    campaign_id = uuid4()
    async with _real_campaign_session() as session:
        await _seed_definition(session, campaign_id=campaign_id, version=3, status="READY", allowed_instruments=["BTC-USD"])
        await _seed_definition(session, campaign_id=campaign_id, version=4, status="DRAFT", allowed_instruments=["BTC-USD", "ETH-USD"])
        await _seed_runtime(session, campaign_id=campaign_id, definition_version=3)
        await session.flush()

        result = await list_campaign_definitions(db=session, campaign_id=None, status=None, latest_only=True)

    assert len(result.items) == 1
    assert result.items[0].version == 3
    assert result.items[0].status == "READY"


@pytest.mark.asyncio
async def test_list_campaign_definitions_explicit_status_filter_path_is_unchanged() -> None:
    """Scope check on the fix itself: passing an explicit `status` must keep
    using the exact original version-equality path (item.version ==
    runtime.definition_version), never the new pin-resolution branch. Using
    a single-version campaign (no DRAFT-successor complication) isolates
    that: the new branch only ever activates for latest_only=True AND
    status=None, so this must still resolve normally."""
    from app.services.capital_campaign_domain.service import list_campaign_definitions

    campaign_id = uuid4()
    async with _real_campaign_session() as session:
        await _seed_definition(session, campaign_id=campaign_id, version=1, status="READY", allowed_instruments=["BTC-USD"])
        await _seed_runtime(session, campaign_id=campaign_id, definition_version=1)
        await session.flush()

        result = await list_campaign_definitions(db=session, campaign_id=None, status="READY", latest_only=True)

    assert len(result.items) == 1
    assert result.items[0].version == 1
    assert result.items[0].status == "READY"


@pytest.mark.asyncio
async def test_list_campaign_definitions_status_filter_combined_with_draft_successor_is_a_pre_existing_limitation() -> None:
    """Documents, rather than fixes, a separate and genuinely pre-existing
    limitation this task did not touch: CapitalCampaignDomainRepository.list's
    latest_only subquery computes the max version per campaign_id BEFORE any
    status filter is applied, so an explicit status query can only ever
    match a row that is also the single highest-numbered version overall.
    While a DRAFT successor (a higher version number) exists, an explicit
    status query for the governing version's own status (here: "READY",
    version 3's real status) returns nothing -- not because of this fix,
    but because repository.list itself never even considers version 3 once
    version 4 exists. Confirmed identical before and after this change since
    this fix only touches the status=None branch."""
    from app.services.capital_campaign_domain.service import list_campaign_definitions

    campaign_id = uuid4()
    async with _real_campaign_session() as session:
        await _seed_definition(session, campaign_id=campaign_id, version=3, status="READY", allowed_instruments=["BTC-USD"])
        await _seed_definition(session, campaign_id=campaign_id, version=4, status="DRAFT", allowed_instruments=["BTC-USD", "ETH-USD"])
        await _seed_runtime(session, campaign_id=campaign_id, definition_version=3)
        await session.flush()

        result = await list_campaign_definitions(db=session, campaign_id=None, status="READY", latest_only=True)

    assert result.items == []


@pytest.mark.asyncio
async def test_list_campaign_definitions_single_version_campaign_unaffected() -> None:
    """Baseline: a campaign with no DRAFT successor at all must behave
    exactly as before -- this fix only changes behavior when the raw
    "latest by number" row and the runtime's actual pin diverge."""
    from app.services.capital_campaign_domain.service import list_campaign_definitions

    campaign_id = uuid4()
    async with _real_campaign_session() as session:
        await _seed_definition(session, campaign_id=campaign_id, version=1, status="READY", allowed_instruments=["BTC-USD"])
        await _seed_runtime(session, campaign_id=campaign_id, definition_version=1)
        await session.flush()

        result = await list_campaign_definitions(db=session, campaign_id=None, status=None, latest_only=True)

    assert len(result.items) == 1
    assert result.items[0].version == 1
