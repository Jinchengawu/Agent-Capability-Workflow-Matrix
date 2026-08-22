import sqlite3
from pathlib import Path

import pytest

from acwm.adapters import GraphRunVersionConflict, SQLiteStore
from acwm.domain import (
    JourneyDefinition,
    JourneySnapshot,
    RepositorySpec,
    StageDefinition,
    compile_journey_graph,
    create_graph_run,
    start_graph_node,
)


async def test_event_log_rebuilds_deleted_snapshot_cache(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "acwm.sqlite")
    await store.initialize()
    snapshot = JourneySnapshot(
        id="journey-1",
        definition_id="code-delivery-v1",
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


async def test_graph_run_survives_restart_and_rejects_a_stale_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "acwm.sqlite"
    first = SQLiteStore(database)
    await first.initialize()
    graph = compile_journey_graph(
        JourneyDefinition(
            id="restartable",
            version="4.0.0",
            nodes=(
                StageDefinition(
                    id="plan",
                    workflow_mode="agentscope.role-turn",
                    bindings={"actor": "hermes-pm"},
                ),
            ),
            edges=(),
        )
    )
    created = create_graph_run("graph-run-1", graph)
    await first.save_graph_run(created, "graph-run.created", expected_version=0)

    restarted = SQLiteStore(database)
    await restarted.initialize()
    recovered = await restarted.get_graph_run("graph-run-1")

    assert recovered == created
    assert recovered is not None
    running = start_graph_node(recovered, "plan")
    await restarted.save_graph_run(
        running, "graph-node.started", expected_version=recovered.version
    )
    with pytest.raises(GraphRunVersionConflict):
        await first.save_graph_run(
            running, "graph-node.started", expected_version=recovered.version
        )


async def test_initialize_upgrades_existing_schema_four_for_graph_runs(tmp_path: Path) -> None:
    database = tmp_path / "acwm.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version(version) VALUES(4);
            CREATE TABLE journeys(
              id TEXT PRIMARY KEY,status TEXT NOT NULL,snapshot_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )

    await SQLiteStore(database).initialize()

    with sqlite3.connect(database) as connection:
        version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
        graph_runs = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='graph_runs'"
        ).fetchone()
    assert version == 5
    assert graph_runs == ("graph_runs",)
