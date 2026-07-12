from __future__ import annotations


def parse_strategy_identity(identity: str) -> tuple[str, str] | None:
    normalized = identity.strip()
    if normalized.count("@") != 1:
        return None
    slug, module_version = normalized.split("@", 1)
    slug = slug.strip()
    module_version = module_version.strip()
    if not slug or not module_version:
        return None
    return slug, module_version


def is_strategy_identity(identity: str) -> bool:
    return parse_strategy_identity(identity) is not None


def build_strategy_identity(*, slug: str, module_version: str) -> str:
    normalized_slug = slug.strip()
    normalized_version = module_version.strip()
    if not normalized_slug or not normalized_version:
        raise ValueError("strategy slug and module version are required")
    return f"{normalized_slug}@{normalized_version}"