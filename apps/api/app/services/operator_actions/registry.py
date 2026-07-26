from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operator_action import OperatorAction


@dataclass(frozen=True, slots=True)
class OperatorActionSubmission:
    """What a handler's submit() reports back after delegating to its
    domain service: which real resource now backs this action."""

    linked_resource_type: str
    linked_resource_id: Any


@dataclass(frozen=True, slots=True)
class OperatorActionProjection:
    """What a handler's project() derives from the linked resource's
    current real state, for the API response -- never persisted verbatim,
    only the status is written back, and only when it changed."""

    status: str
    result: dict[str, Any] | None
    blocked_reason: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class OperatorActionHandler:
    """One action_type's entire contract: a strict parameter schema (extra
    fields rejected), a submit step that delegates to the existing domain
    service exactly once, and a status projector that derives the action's
    current status/result from the linked resource's real state. No
    execution or orchestration logic belongs here -- only delegation and
    projection."""

    parameters_model: type[BaseModel]
    submit: Callable[[AsyncSession, OperatorAction, BaseModel], Awaitable[OperatorActionSubmission]]
    project: Callable[[AsyncSession, OperatorAction], Awaitable[OperatorActionProjection]]


_REGISTRY: dict[str, OperatorActionHandler] = {}


def register_action_handler(action_type: str, handler: OperatorActionHandler) -> None:
    _REGISTRY[action_type] = handler


def get_action_handler(action_type: str) -> OperatorActionHandler | None:
    return _REGISTRY.get(action_type)


def registered_action_types() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY.keys()))
