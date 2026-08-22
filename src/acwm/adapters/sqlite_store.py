"""SQLite event log and rebuildable Journey snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from acwm.domain import ExecutionEvent, GraphRun, JourneySnapshot


class IdempotencyConflictError(ValueError):
    pass


class GraphRunVersionConflict(ValueError):
    pass


class LegacyDataDirError(RuntimeError):
    code = "legacy_data_dir_unsupported"


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            has_version = await db.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )
            if has_version:
                rows = list(await db.execute_fetchall("SELECT version FROM schema_version"))
                if not rows or int(rows[0][0]) not in {4, 5}:
                    raise LegacyDataDirError(
                        "ACWM v0.4 requires schema 4 or 5; older data is unsupported"
                    )
                if int(rows[0][0]) == 4:
                    await db.executescript(_GRAPH_RUN_SCHEMA)
                    await db.execute("UPDATE schema_version SET version=5")
                    await db.commit()
                return
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
                INSERT INTO schema_version(version) VALUES(5);
                CREATE TABLE IF NOT EXISTS journeys(
                  id TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  snapshot_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  journey_id TEXT NOT NULL,
                  type TEXT NOT NULL,
                  entity_type TEXT NOT NULL,
                  entity_id TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  snapshot_json TEXT,
                  timestamp TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_by_journey ON events(journey_id, id);
                CREATE TABLE IF NOT EXISTS idempotency(
                  key TEXT PRIMARY KEY,
                  body_hash TEXT NOT NULL,
                  status_code INTEGER NOT NULL,
                  response_json TEXT NOT NULL
                );
                """
            )
            await db.executescript(_GRAPH_RUN_SCHEMA)
            await db.commit()

    async def save_graph_run(
        self,
        run: GraphRun,
        event_type: str,
        *,
        expected_version: int,
        payload: dict[str, Any] | None = None,
    ) -> None:
        timestamp = datetime.now(UTC).isoformat()
        encoded = run.model_dump_json()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT version FROM graph_runs WHERE id=?", (run.id,)
            )
            row = await cursor.fetchone()
            actual_version = int(row[0]) if row else 0
            if actual_version != expected_version:
                await db.rollback()
                raise GraphRunVersionConflict(
                    f"Graph Run {run.id} expected version {expected_version}, "
                    f"actual {actual_version}"
                )
            await db.execute(
                """INSERT INTO graph_runs(id,status,version,snapshot_json,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,version=excluded.version,
                snapshot_json=excluded.snapshot_json,updated_at=excluded.updated_at""",
                (run.id, run.status, run.version, encoded, timestamp),
            )
            await db.execute(
                """INSERT INTO graph_run_events(
                graph_run_id,type,run_version,payload_json,snapshot_json,timestamp)
                VALUES(?,?,?,?,?,?)""",
                (
                    run.id,
                    event_type,
                    run.version,
                    json.dumps(self._redact(payload or {}), sort_keys=True),
                    encoded,
                    timestamp,
                ),
            )
            await db.commit()

    async def get_graph_run(self, run_id: str) -> GraphRun | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT snapshot_json FROM graph_runs WHERE id=?", (run_id,)
            )
            row = await cursor.fetchone()
        return GraphRun.model_validate_json(row[0]) if row else None

    async def append_event(
        self,
        journey_id: str,
        event_type: str,
        *,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        timestamp = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO events(
                journey_id,type,entity_type,entity_id,payload_json,snapshot_json,timestamp
                ) VALUES(?,?,?,?,?,NULL,?)""",
                (
                    journey_id,
                    event_type,
                    entity_type,
                    entity_id,
                    json.dumps(self._redact(payload or {}), sort_keys=True),
                    timestamp,
                ),
            )
            await db.commit()

    async def save(
        self,
        snapshot: JourneySnapshot,
        event_type: str,
        *,
        entity_type: str = "journey",
        entity_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        timestamp = datetime.now(UTC).isoformat()
        encoded = snapshot.model_dump_json(exclude_computed_fields=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO journeys(id,status,snapshot_json,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                snapshot_json=excluded.snapshot_json,updated_at=excluded.updated_at""",
                (snapshot.id, snapshot.status.value, encoded, timestamp),
            )
            await db.execute(
                """INSERT INTO events(
                journey_id,type,entity_type,entity_id,payload_json,snapshot_json,timestamp
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    snapshot.id,
                    event_type,
                    entity_type,
                    entity_id or snapshot.id,
                    json.dumps(self._redact(payload or {}), sort_keys=True),
                    encoded,
                    timestamp,
                ),
            )
            await db.commit()

    async def get(self, journey_id: str) -> JourneySnapshot | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT snapshot_json FROM journeys WHERE id=?", (journey_id,)
            )
            row = await cursor.fetchone()
        return JourneySnapshot.model_validate_json(row[0]) if row else None

    async def list_snapshots(self) -> list[JourneySnapshot]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT snapshot_json FROM journeys ORDER BY updated_at")
            rows = await cursor.fetchall()
        return [JourneySnapshot.model_validate_json(row[0]) for row in rows]

    async def clear_snapshot_cache(self) -> None:
        """Clear only the rebuildable projection, never the authoritative event log."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM journeys")
            await db.commit()

    async def rebuild_snapshot_cache(self) -> int:
        """Recreate Journey projections from the latest snapshot carried by each event."""
        async with aiosqlite.connect(self.path) as db:
            rows = list(
                await db.execute_fetchall(
                    """SELECT e.journey_id, e.snapshot_json, e.timestamp
                FROM events e
                JOIN (
                  SELECT journey_id, MAX(id) AS max_id
                  FROM events WHERE snapshot_json IS NOT NULL GROUP BY journey_id
                ) latest ON latest.max_id=e.id
                ORDER BY e.id"""
                )
            )
            await db.execute("BEGIN IMMEDIATE")
            for journey_id, snapshot_json, timestamp in rows:
                snapshot = JourneySnapshot.model_validate_json(snapshot_json)
                await db.execute(
                    """INSERT INTO journeys(id,status,snapshot_json,updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                    snapshot_json=excluded.snapshot_json,updated_at=excluded.updated_at""",
                    (journey_id, snapshot.status.value, snapshot_json, timestamp),
                )
            await db.commit()
        return len(rows)

    async def events(self, journey_id: str, after: int = 0) -> list[ExecutionEvent]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """SELECT id,type,entity_type,entity_id,payload_json,timestamp
                FROM events WHERE journey_id=? AND id>? ORDER BY id""",
                (journey_id, after),
            )
            rows = await cursor.fetchall()
        return [
            ExecutionEvent(
                event_id=row[0],
                journey_id=journey_id,
                type=row[1],
                entity_type=row[2],
                entity_id=row[3],
                payload=json.loads(row[4]),
                timestamp=datetime.fromisoformat(row[5]),
            )
            for row in rows
        ]

    async def idempotent_response(
        self, key: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]] | None:
        body_hash = self._hash(body)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT body_hash,status_code,response_json FROM idempotency WHERE key=?", (key,)
            )
            row = await cursor.fetchone()
        if not row:
            return None
        if row[0] != body_hash:
            raise IdempotencyConflictError("Idempotency-Key was reused with a different body")
        return int(row[1]), json.loads(row[2])

    async def remember_response(
        self, key: str, body: dict[str, Any], status_code: int, response: dict[str, Any]
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO idempotency(key,body_hash,status_code,response_json) VALUES(?,?,?,?)",
                (key, self._hash(body), status_code, json.dumps(response, sort_keys=True)),
            )
            await db.commit()

    @staticmethod
    def _hash(body: dict[str, Any]) -> str:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _redact(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if cls._is_sensitive_key(key) else cls._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list | tuple):
            return [cls._redact(item) for item in value]
        return value

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        return any(
            marker in normalized
            for marker in ("secret", "password", "token", "api_key", "authorization")
        )


_GRAPH_RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_runs(
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  version INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_run_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  graph_run_id TEXT NOT NULL,
  type TEXT NOT NULL,
  run_version INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS graph_run_events_by_run
ON graph_run_events(graph_run_id, id);
"""
