import pytest

from acwm.domain import (
    GateSnapshot,
    GateStatus,
    GateSubject,
    StaleGateDecision,
    decide_gate,
    open_gate,
)

SUBJECT_HASH = "a" * 64


def test_gate_decision_is_bound_to_a_generic_immutable_subject() -> None:
    opened = open_gate(
        GateSnapshot(id="approve-candidate", subject_kind="candidate-change"),
        subject=GateSubject(
            kind="candidate-change",
            artifact_id="candidate-7",
            sha256=SUBJECT_HASH,
        ),
        revision=4,
    )

    approved = decide_gate(
        opened,
        decision="approve",
        expected_revision=4,
        expected_subject_hash=SUBJECT_HASH,
    )

    assert approved.status is GateStatus.APPROVED
    assert approved.subject.artifact_id == "candidate-7"  # type: ignore[union-attr]

    with pytest.raises(StaleGateDecision):
        decide_gate(
            opened,
            decision="approve",
            expected_revision=3,
            expected_subject_hash=SUBJECT_HASH,
        )

    with pytest.raises(StaleGateDecision):
        decide_gate(
            opened,
            decision="approve",
            expected_revision=4,
            expected_subject_hash="b" * 64,
        )
