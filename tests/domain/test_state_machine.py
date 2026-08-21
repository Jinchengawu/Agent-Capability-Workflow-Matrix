import pytest

from acwm.domain import (
    AttemptStatus,
    DomainTransitionError,
    JourneyStatus,
    transition_attempt,
    transition_journey,
)


def test_journey_and_attempt_statuses_only_follow_legal_transitions() -> None:
    assert transition_journey(JourneyStatus.QUEUED, JourneyStatus.RUNNING) is JourneyStatus.RUNNING
    assert (
        transition_journey(JourneyStatus.RUNNING, JourneyStatus.AWAITING_APPROVAL)
        is JourneyStatus.AWAITING_APPROVAL
    )
    assert (
        transition_attempt(AttemptStatus.RUNNING, AttemptStatus.INTERRUPTED)
        is AttemptStatus.INTERRUPTED
    )

    with pytest.raises(DomainTransitionError):
        transition_attempt(AttemptStatus.INTERRUPTED, AttemptStatus.RUNNING)

    with pytest.raises(DomainTransitionError):
        transition_journey(JourneyStatus.COMPLETED, JourneyStatus.RUNNING)
