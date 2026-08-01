from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.services.pipeline_contracts.envelope import (
    CANONICAL_ENVELOPE_SCHEMA_VERSION,
    CanonicalEnvelopeV1,
    QualityStatus,
)
from app.services.pipeline_contracts.identifiers import CorrelationId, EventId, RunId
from app.services.pipeline_contracts.serialization import (
    canonical_json,
    canonical_json_bytes,
    integrity_sha256,
    integrity_sha256_excluding_root_integrity_hash,
)


EVENT_ID = EventId(value=UUID("11111111-1111-4111-8111-111111111111"))
RUN_ID = RunId(value=UUID("22222222-2222-4222-8222-222222222222"))
CORRELATION_ID = CorrelationId(value=UUID("33333333-3333-4333-8333-333333333333"))
OCCURRED_AT = datetime(2026, 7, 15, 12, 0, 0, 123456, tzinfo=timezone.utc)


def _envelope(**overrides: object) -> CanonicalEnvelopeV1:
    values: dict[str, object] = {
        "event_id": EVENT_ID,
        "event_type": "market_observation",
        "schema_version": CANONICAL_ENVELOPE_SCHEMA_VERSION,
        "source": "fixture:test",
        "occurred_at": OCCURRED_AT,
        "available_at": None,
        "received_at": OCCURRED_AT,
        "correlation_id": CORRELATION_ID,
        "causation_id": None,
        "run_id": RUN_ID,
        "stage_version": "test-stage/v1",
        "quality_status": QualityStatus.ACCEPTED,
    }
    values.update(overrides)
    return CanonicalEnvelopeV1.model_validate(values)


def test_canonical_serialization_is_byte_stable_and_key_order_independent() -> None:
    first = _envelope()
    reordered = CanonicalEnvelopeV1.model_validate(dict(reversed(list(first.model_dump().items()))))
    assert first.canonical_bytes() == reordered.canonical_bytes()
    assert first.canonical_bytes() == first.canonical_bytes()
    assert first.canonical_bytes().decode("utf-8").startswith('{"available_at":null,')


def test_list_order_is_preserved_but_mapping_order_is_not() -> None:
    left = {"items": ["first", "second"], "mapping": {"b": 2, "a": 1}}
    same = {"mapping": {"a": 1, "b": 2}, "items": ["first", "second"]}
    changed = {"items": ["second", "first"], "mapping": {"a": 1, "b": 2}}
    assert canonical_json_bytes(left) == canonical_json_bytes(same)
    assert canonical_json_bytes(left) != canonical_json_bytes(changed)


def test_decimal_never_uses_float_and_preserves_meaningful_scale() -> None:
    assert canonical_json({"amount": Decimal("1.2300")}) == '{"amount":"1.2300"}'
    assert canonical_json({"amount": Decimal("1.23")}) != canonical_json({"amount": Decimal("1.2300")})
    with pytest.raises(TypeError, match="binary floating-point"):
        canonical_json({"amount": 1.23})


def test_aware_datetime_normalizes_to_utc_and_naive_is_rejected() -> None:
    offset = timezone(timedelta(hours=-7))
    local = datetime(2026, 7, 15, 5, 0, 0, 123456, tzinfo=offset)
    assert canonical_json({"at": local}) == '{"at":"2026-07-15T12:00:00.123456Z"}'
    with pytest.raises(ValueError, match="naive"):
        canonical_json({"at": datetime(2026, 7, 15, 12, 0)})
    with pytest.raises(ValidationError, match="timezone-aware"):
        _envelope(occurred_at=datetime(2026, 7, 15, 12, 0))


def test_uuid_and_enum_serialization_are_deterministic() -> None:
    class Example(str, Enum):
        VALUE = "VALUE"

    assert canonical_json({"enum": Example.VALUE, "uuid": EVENT_ID.value}) == (
        '{"enum":"VALUE","uuid":"11111111-1111-4111-8111-111111111111"}'
    )


def test_root_integrity_hash_is_reproducible_sensitive_and_non_recursive() -> None:
    original = _envelope()
    digest = original.computed_integrity_hash()
    assert digest == original.computed_integrity_hash()
    assert digest != _envelope(event_type="different").computed_integrity_hash()
    populated = original.with_computed_integrity_hash()
    assert isinstance(populated, CanonicalEnvelopeV1)
    assert populated.integrity_hash == digest
    assert populated.computed_integrity_hash() == digest
    assert '"integrity_hash":"' + digest + '"' in populated.canonical_bytes().decode("utf-8")
    assert integrity_sha256_excluding_root_integrity_hash(
        {"value": "same", "integrity_hash": "0" * 64}
    ) == integrity_sha256_excluding_root_integrity_hash(
        {"value": "same", "integrity_hash": "f" * 64}
    )


def test_nested_integrity_hash_and_every_other_nested_field_remain_hashed() -> None:
    baseline = {
        "integrity_hash": "root-placeholder",
        "payload": {"integrity_hash": "nested-a", "meaningful": "value-a"},
    }
    nested_hash_changed = {
        "integrity_hash": "different-root-placeholder",
        "payload": {"integrity_hash": "nested-b", "meaningful": "value-a"},
    }
    nested_value_changed = {
        "integrity_hash": "root-placeholder",
        "payload": {"integrity_hash": "nested-a", "meaningful": "value-b"},
    }
    baseline_digest = integrity_sha256_excluding_root_integrity_hash(baseline)
    assert baseline_digest != integrity_sha256_excluding_root_integrity_hash(nested_hash_changed)
    assert baseline_digest != integrity_sha256_excluding_root_integrity_hash(nested_value_changed)
    assert canonical_json(baseline) == (
        '{"integrity_hash":"root-placeholder","payload":'
        '{"integrity_hash":"nested-a","meaningful":"value-a"}}'
    )
    assert integrity_sha256(baseline) != integrity_sha256(
        {**baseline, "integrity_hash": "different-root-placeholder"}
    )


def test_known_schema_version_is_required_and_unknown_fails_closed() -> None:
    assert _envelope().schema_version == CANONICAL_ENVELOPE_SCHEMA_VERSION
    values = _envelope().model_dump()
    values.pop("schema_version")
    with pytest.raises(ValidationError):
        CanonicalEnvelopeV1.model_validate(values)
    with pytest.raises(ValidationError):
        _envelope(schema_version="canonical-envelope/v2")


def test_bad_supplied_integrity_hash_fails_closed() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        _envelope(integrity_hash="0" * 64)
