from __future__ import annotations

from app import main as api_main
from app.db import session as session_module
from app.services.data import worker_entrypoint
from app.services.orchestration import continuous_pipeline_worker
from scripts import replace_governing_mandate_version as replacement_script


def test_api_uses_shared_async_sessionlocal() -> None:
    assert api_main.AsyncSessionLocal is session_module.AsyncSessionLocal


def test_worker_entrypoints_use_shared_async_sessionlocal() -> None:
    assert worker_entrypoint.AsyncSessionLocal is session_module.AsyncSessionLocal
    assert continuous_pipeline_worker.AsyncSessionLocal is session_module.AsyncSessionLocal


def test_replacement_script_uses_shared_async_sessionlocal() -> None:
    assert replacement_script.AsyncSessionLocal is session_module.AsyncSessionLocal
