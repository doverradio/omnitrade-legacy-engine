from __future__ import annotations

from datetime import datetime, timezone

# In-memory status is acceptable for local dev/MVP where worker and API may share process memory.
# When worker and API run in separate deployments, this must move to a shared DB/cache-backed store.
_last_successful_ingestion_at: datetime | None = None
_last_successful_full_pipeline_at: datetime | None = None


def get_last_successful_ingestion_at() -> datetime | None:
    return _last_successful_ingestion_at


def set_last_successful_ingestion_at(value: datetime) -> None:
    global _last_successful_ingestion_at

    if value.tzinfo is None:
        _last_successful_ingestion_at = value.replace(tzinfo=timezone.utc)
    else:
        _last_successful_ingestion_at = value.astimezone(timezone.utc)


def reset_last_successful_ingestion_at() -> None:
    global _last_successful_ingestion_at
    _last_successful_ingestion_at = None


def get_last_successful_full_pipeline_at() -> datetime | None:
    return _last_successful_full_pipeline_at


def set_last_successful_full_pipeline_at(value: datetime) -> None:
    global _last_successful_full_pipeline_at

    if value.tzinfo is None:
        _last_successful_full_pipeline_at = value.replace(tzinfo=timezone.utc)
    else:
        _last_successful_full_pipeline_at = value.astimezone(timezone.utc)


def reset_last_successful_full_pipeline_at() -> None:
    global _last_successful_full_pipeline_at
    _last_successful_full_pipeline_at = None
