from pydantic import ValidationError

from acwm.domain import ArtifactRef, HandoffEnvelope


def test_handoff_is_immutable_and_self_verifying() -> None:
    envelope = HandoffEnvelope.create(
        objective="Add a health endpoint",
        summary="The direct stage produced an implementation plan.",
        decisions=("Use FastAPI",),
        constraints=("Do not modify the main checkout",),
        facts=("The project uses Python 3.11",),
        open_items=("Confirm response schema",),
        source_journey_id="journey-1",
        source_stage_id="plan",
        source_attempt_id="attempt-1",
        artifacts=(
            ArtifactRef(
                artifact_id="artifact-1",
                kind="implementation_plan",
                media_type="text/markdown",
                sha256="a" * 64,
                uri="artifact://artifact-1",
            ),
        ),
    )

    assert envelope.verify()
    assert envelope.sha256 == HandoffEnvelope.compute_hash(envelope.payload())

    try:
        envelope.summary = "tampered"  # type: ignore[misc]
    except ValidationError:
        pass
    else:
        raise AssertionError("HandoffEnvelope must be immutable")
