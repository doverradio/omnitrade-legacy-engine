from __future__ import annotations

import inspect
from pathlib import Path

import app.api.routes.capital_campaigns as campaign_routes
import app.operator_cli.service as operator_service


def _source_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_rest_and_cli_call_shared_commissioned_control_plane_service() -> None:
    rest_status_source = inspect.getsource(campaign_routes.get_capital_campaign_domain_commissioned_control_plane_status)
    rest_action_source = inspect.getsource(campaign_routes.post_capital_campaign_domain_commissioned_control_plane_action)
    cli_status_source = inspect.getsource(operator_service.fetch_commissioned_control_plane_status)
    cli_action_source = inspect.getsource(operator_service.mutate_commissioned_control_plane_action)

    assert "get_commissioned_control_plane_status" in rest_status_source
    assert "mutate_commissioned_control_plane" in rest_action_source
    assert "_get_commissioned_control_plane_status" in cli_status_source
    assert "_mutate_commissioned_control_plane" in cli_action_source


def test_control_plane_layers_do_not_call_provider_adapters_directly() -> None:
    commissioned_service = _source_text("/home/eric/omnitrade-legacy-engine/apps/api/app/services/capital_campaign_domain/commissioned_control_plane.py")
    rest_routes = _source_text("/home/eric/omnitrade-legacy-engine/apps/api/app/api/routes/capital_campaigns.py")
    cli_service = _source_text("/home/eric/omnitrade-legacy-engine/apps/api/app/operator_cli/service.py")

    forbidden_fragments = [
        "CoinbaseAdvancedClient",
        "Kraken",
        "create_order(",
        "submit_order(",
        "from app.providers",
        "import app.providers",
    ]

    for token in forbidden_fragments:
        assert token not in commissioned_service

    # Route and CLI wrappers for commissioned control-plane stay orchestration-free.
    commissioned_routes_slice = "\n".join(
        line for line in rest_routes.splitlines() if "commissioned/control-plane" in line or "commissioned_control_plane" in line
    )
    commissioned_cli_slice = "\n".join(
        line for line in cli_service.splitlines() if "commissioned_control_plane" in line
    )

    for token in forbidden_fragments:
        assert token not in commissioned_routes_slice
        assert token not in commissioned_cli_slice
