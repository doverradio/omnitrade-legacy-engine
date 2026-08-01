"""Deterministic serialization primitives for non-authoritative contracts.

Decimal scale is meaningful and preserved (``1.0`` differs from ``1.00``).
Datetimes must be timezone-aware and serialize in UTC with six fractional
digits. Model ``None`` fields are retained; absent mapping keys remain absent.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping
from uuid import UUID

from pydantic import BaseModel


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("canonical serialization rejects naive datetimes")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_value(value: Any) -> Any:
    """Convert supported values to a JSON-compatible deterministic tree."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", exclude_none=False)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            normalized[key] = canonical_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical serialization requires finite Decimal values")
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        raise TypeError("binary floating-point values are not canonical; use Decimal")
    raise TypeError(f"unsupported canonical serialization type: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    tree = canonical_value(value)
    return json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def integrity_sha256(value: Any) -> str:
    """Hash every field in the supplied value's canonical representation."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def integrity_sha256_excluding_root_integrity_hash(value: BaseModel | Mapping[str, Any]) -> str:
    """Hash an object's content except its designated root self-hash field.

    Only the root mapping is copied and stripped. Nested ``integrity_hash``
    fields remain meaningful canonical data and are always hashed.
    """

    if isinstance(value, BaseModel):
        root = value.model_dump(mode="python", exclude_none=False)
    elif isinstance(value, Mapping):
        root = dict(value)
    else:
        raise TypeError("root self-hash exclusion requires a model or mapping")
    root.pop("integrity_hash", None)
    return integrity_sha256(root)
