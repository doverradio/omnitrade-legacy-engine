from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict


class ParameterSetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_id: uuid.UUID
    name: str
    parameters: dict[str, Any]


class ParameterSetListResponse(BaseModel):
    items: list[ParameterSetResponse]