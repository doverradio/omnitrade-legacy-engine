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
