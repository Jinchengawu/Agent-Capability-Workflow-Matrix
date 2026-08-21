from pathlib import Path

from acwm.adapters import SQLiteStore
from acwm.domain import JourneySnapshot, RepositorySpec


async def test_event_log_rebuilds_deleted_snapshot_cache(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "acwm.sqlite")
    await store.initialize()
    snapshot = JourneySnapshot(
        id="journey-1",
        definition_id="code-delivery-v1",
        capability_id="hermes-developer",
        objective="Rebuild me",
        repository=RepositorySpec(path=str(tmp_path), base_ref="HEAD"),
        stages=(),
        gates=(),
    )
    await store.save(snapshot, "journey.created")

    await store.clear_snapshot_cache()
    assert await store.get(snapshot.id) is None

    rebuilt = await store.rebuild_snapshot_cache()

    assert rebuilt == 1
    assert await store.get(snapshot.id) == snapshot
