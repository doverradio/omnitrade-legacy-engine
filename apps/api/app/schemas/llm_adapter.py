from __future__ import annotations

import uuid

from pydantic import BaseModel


class LLMAdapterResponse(BaseModel):
    adapter_id: uuid.UUID
    adapter_name: str
    provider: str
    capabilities: list[str]
    status: str
