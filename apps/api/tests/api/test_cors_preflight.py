from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def test_cors_preflight_allows_app_origin(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,https://app.bigdeal.sale")
    get_settings.cache_clear()

    app = create_app()

    with TestClient(app) as client:
        response = client.options(
            "/paper/pipeline-health",
            headers={
                "Origin": "https://app.bigdeal.sale",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://app.bigdeal.sale"

    get_settings.cache_clear()


def test_cors_default_settings_include_production_app_origin(monkeypatch) -> None:
    """Guards the canonical config default itself (no env override): the
    production frontend origin must be present out of the box, not only
    when an operator remembers to set CORS_ALLOWED_ORIGINS explicitly."""
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert "https://app.bigdeal.sale" in settings.parsed_cors_allowed_origins

    get_settings.cache_clear()


def test_cors_preflight_allows_capital_campaigns_domain_route(monkeypatch) -> None:
    """Reproduces the exact reported production failure: the /workflow
    page's PROCESS tab reads /capital-campaigns/domain from the browser."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,https://app.bigdeal.sale")
    get_settings.cache_clear()

    app = create_app()

    with TestClient(app) as client:
        preflight = client.options(
            "/capital-campaigns/domain",
            headers={
                "Origin": "https://app.bigdeal.sale",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == "https://app.bigdeal.sale"

    get_settings.cache_clear()


def test_cors_preflight_rejects_unapproved_origin(monkeypatch) -> None:
    """An explicit allowlist, not a wildcard: an origin that was never
    configured must not receive an Access-Control-Allow-Origin header, on
    this or any other route."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,https://app.bigdeal.sale")
    get_settings.cache_clear()

    app = create_app()

    with TestClient(app) as client:
        response = client.options(
            "/paper/pipeline-health",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    # Starlette's CORSMiddleware still returns 200 for a disallowed-origin
    # preflight (it never 4xx/5xxs the OPTIONS request itself) -- the
    # enforcement is the ABSENCE of the allow-origin header, which is what
    # actually makes the browser block the real request.
    assert response.headers.get("access-control-allow-origin") is None

    get_settings.cache_clear()
