from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict


class StrategyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    module_version: str
    default_params: dict[str, Any] | None = None


class StrategyListResponse(BaseModel):
    items: list[StrategyResponse]
