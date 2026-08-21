"""ACWM lifecycle state machines."""

from enum import StrEnum
from typing import TypeVar


class JourneyStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_PERMISSION = "awaiting_permission"
    CANCELLING = "cancelling"
    NEEDS_ATTENTION = "needs_attention"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class GateStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class StageStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class DomainTransitionError(ValueError):
    pass


_JOURNEY_TRANSITIONS = {
    JourneyStatus.QUEUED: {JourneyStatus.RUNNING, JourneyStatus.CANCELLED},
    JourneyStatus.RUNNING: {
        JourneyStatus.AWAITING_APPROVAL,
        JourneyStatus.AWAITING_PERMISSION,
        JourneyStatus.NEEDS_ATTENTION,
        JourneyStatus.COMPLETED,
        JourneyStatus.FAILED,
        JourneyStatus.CANCELLED,
        JourneyStatus.CANCELLING,
    },
    JourneyStatus.AWAITING_APPROVAL: {JourneyStatus.RUNNING, JourneyStatus.CANCELLED},
    JourneyStatus.AWAITING_PERMISSION: {
        JourneyStatus.RUNNING,
        JourneyStatus.NEEDS_ATTENTION,
        JourneyStatus.FAILED,
        JourneyStatus.CANCELLED,
    },
    JourneyStatus.CANCELLING: {JourneyStatus.CANCELLED, JourneyStatus.NEEDS_ATTENTION},
    JourneyStatus.NEEDS_ATTENTION: {
        JourneyStatus.RUNNING,
        JourneyStatus.COMPLETED,
        JourneyStatus.FAILED,
        JourneyStatus.CANCELLED,
    },
    JourneyStatus.COMPLETED: set(),
    JourneyStatus.FAILED: set(),
    JourneyStatus.CANCELLED: set(),
}

_ATTEMPT_TRANSITIONS = {
    AttemptStatus.QUEUED: {AttemptStatus.RUNNING, AttemptStatus.CANCELLED},
    AttemptStatus.RUNNING: {
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.INTERRUPTED,
        AttemptStatus.CANCELLED,
        AttemptStatus.CANCELLING,
    },
    AttemptStatus.CANCELLING: {AttemptStatus.CANCELLED, AttemptStatus.FAILED},
    AttemptStatus.SUCCEEDED: set(),
    AttemptStatus.FAILED: set(),
    AttemptStatus.INTERRUPTED: set(),
    AttemptStatus.CANCELLED: set(),
}

StatusT = TypeVar("StatusT", JourneyStatus, AttemptStatus)


def _transition(current: StatusT, target: StatusT, allowed: dict[StatusT, set[StatusT]]) -> StatusT:
    if target not in allowed[current]:
        raise DomainTransitionError(f"Illegal transition: {current.value} -> {target.value}")
    return target


def transition_journey(current: JourneyStatus, target: JourneyStatus) -> JourneyStatus:
    return _transition(current, target, _JOURNEY_TRANSITIONS)


def transition_attempt(current: AttemptStatus, target: AttemptStatus) -> AttemptStatus:
    return _transition(current, target, _ATTEMPT_TRANSITIONS)
