from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.capital_ledger import CapitalLedgerResponse, CapitalLedgerSummaryResponse, CapitalPoolResponse


class _DummySession:
    pass


def _payload() -> CapitalLedgerResponse:
    return CapitalLedgerResponse(
        summary=CapitalLedgerSummaryResponse(
            total_managed_capital=Decimal("50"),
            total_starting_capital=Decimal("50"),
            total_current_equity=Decimal("50"),
            total_allocated_capital=Decimal("50"),
            total_available_capital=Decimal("0"),
            total_reserved_capital=Decimal("50"),
            total_realized_pnl=Decimal("0"),
            total_unrealized_pnl=Decimal("0"),
            active_capital_pools=2,
            inactive_capital_pools=0,
            active_positions=0,
            total_trades=13,
            utilization_percent=100.0,
            data_completeness_percent=100.0,
            unavailable_sources=[],
            generated_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
        ),
        capital_pools=[
            CapitalPoolResponse(
                capital_pool_id="validation-run:11111111-1111-1111-1111-111111111111",
                capital_pool_type="validation_run",
                name="Run A",
                status="active",
                starting_capital=Decimal("25"),
                current_equity=Decimal("26"),
                allocated_capital=Decimal("25"),
                available_capital=Decimal("1"),
                reserved_capital=Decimal("25"),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("1"),
                pnl_percent=4.0,
                started_at=datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
                completed_at=None,
                related_entity_type="validation_run",
                related_entity_id="11111111-1111-1111-1111-111111111111",
                related_page_url="/validation-runs",
                parent_capital_pool_id=None,
                child_allocations_count=1,
                notes="Top-level funded validation pool.",
            )
        ],
        page=1,
        page_size=50,
        total=1,
        has_more=False,
    )


def test_capital_ledger_route_returns_shape(monkeypatch) -> None:
    app = create_app()
    captured: dict[str, object] = {}

    async def _override_db():
        yield _DummySession()

    async def _service_stub(*_args, **_kwargs):
        captured.update(_kwargs)
        return _payload()

    app.dependency_overrides.clear()
    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr("app.api.routes.capital.build_capital_ledger", _service_stub)

    with TestClient(app) as client:
        response = client.get("/capital/ledger?status=active&type=validation_run&page=1&page_size=20")

    assert response.status_code == 200
    payload = response.json()

    assert payload["summary"]["total_managed_capital"] == "50"
    assert payload["summary"]["active_capital_pools"] == 2
    assert payload["capital_pools"][0]["related_page_url"] == "/validation-runs"
    assert payload["page"] == 1
    assert payload["page_size"] == 50
    assert captured["status"] == "active"
    assert captured["capital_type"] == "validation_run"
    assert captured["page"] == 1
    assert captured["page_size"] == 20
