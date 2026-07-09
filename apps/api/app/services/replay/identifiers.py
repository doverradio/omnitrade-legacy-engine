from __future__ import annotations

import uuid
from hashlib import sha256


def build_decision_package_id(*, decision_id: uuid.UUID, package_hash: str, package_version: str) -> str:
    payload = f"{decision_id}:{package_hash}:{package_version}"
    digest = sha256(payload.encode("ascii"), usedforsecurity=False).hexdigest()
    return f"dpkg:{digest}"
