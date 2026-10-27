"""Persistence of arena runs, cases (with their events) and datasets (IMPLEMENTATION.md §7).

Records are plain JSON documents (dicts). `RunStore` is the interface; `PostgresRunStore` keeps them as JSONB in
the style of `hackathon.challenge_store`, `MemoryRunStore` keeps them in process (tests, `ARENA_STORE=memory`).
Records never contain request headers or credentials (R-EXE-8); the orchestrator only stores what it builds.
"""

import copy
import json
from collections.abc import Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from hackathon.config import get_settings

ACTIVE_STATUSES = frozenset({"generating", "running"})
TERMINAL_STATUSES = frozenset({"completed", "stopped", "failed", "interrupted"})


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def interrupt(run: dict[str, Any], at: str | None = None) -> dict[str, Any]:
    """A run found active at startup: its process is gone, so it becomes `interrupted`."""
    return {
        **run,
        "status": "interrupted",
        "finished_at": run.get("finished_at") or at or now_iso(),
    }


def case_order(case: dict[str, Any]) -> tuple[int, int]:
    """Red cases first, each team by its index."""
    return (0 if case["team"] == "red" else 1, case["index"])


def dataset_meta(dataset: dict[str, Any]) -> dict[str, Any]:
    """A dataset without its cases (`GET /levels/{level}/datasets`)."""
    return {key: value for key, value in dataset.items() if key != "cases"}


def newest_first(records: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(records, key=lambda record: record[key], reverse=True)


class RunStore(Protocol):
    async def setup(self) -> int:
        """Prepare the store; runs left `generating`/`running` become `interrupted`. Returns their number."""
        ...

    async def save_run(self, run: dict[str, Any]) -> None: ...

    async def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    async def list_runs(self, level: str | None = None) -> list[dict[str, Any]]:
        """Runs, newest first."""
        ...

    async def save_case(self, case: dict[str, Any]) -> None:
        """Insert or replace a case including its `events`."""
        ...

    async def list_cases(
        self, run_id: str, *, events: bool = False
    ) -> list[dict[str, Any]]:
        """Cases of a run in display order; `events` is left out unless asked for."""
        ...

    async def get_case(self, run_id: str, case_id: str) -> dict[str, Any] | None: ...

    async def save_dataset(self, dataset: dict[str, Any]) -> None: ...

    async def get_dataset(self, dataset_id: str) -> dict[str, Any] | None: ...

    async def list_datasets(self, level: str) -> list[dict[str, Any]]:
        """Datasets of a level without their cases, newest first."""
        ...


class MemoryRunStore:
    """In-process store. Records are copied in and out, like a database would."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.cases: dict[str, dict[str, Any]] = {}
        self.datasets: dict[str, dict[str, Any]] = {}

    async def setup(self) -> int:
        active = [run for run in self.runs.values() if run["status"] in ACTIVE_STATUSES]
        for run in active:
            self.runs[run["id"]] = interrupt(run)
        return len(active)

    async def save_run(self, run: dict[str, Any]) -> None:
        self.runs[run["id"]] = copy.deepcopy(run)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.runs.get(run_id))

    async def list_runs(self, level: str | None = None) -> list[dict[str, Any]]:
        runs = [
            run for run in self.runs.values() if level is None or run["level"] == level
        ]
        return copy.deepcopy(newest_first(runs, "started_at"))

    async def save_case(self, case: dict[str, Any]) -> None:
        self.cases[case["id"]] = copy.deepcopy(case)

    async def list_cases(
        self, run_id: str, *, events: bool = False
    ) -> list[dict[str, Any]]:
        cases = sorted(
            (case for case in self.cases.values() if case["run_id"] == run_id),
            key=case_order,
        )
        if not events:
            cases = [{k: v for k, v in case.items() if k != "events"} for case in cases]
        return copy.deepcopy(cases)

    async def get_case(self, run_id: str, case_id: str) -> dict[str, Any] | None:
        case = self.cases.get(case_id)
        return copy.deepcopy(case) if case and case["run_id"] == run_id else None

    async def save_dataset(self, dataset: dict[str, Any]) -> None:
        self.datasets[dataset["id"]] = copy.deepcopy(dataset)

    async def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.datasets.get(dataset_id))

    async def list_datasets(self, level: str) -> list[dict[str, Any]]:
        datasets = [d for d in self.datasets.values() if d["level"] == level]
        return [
            dataset_meta(d) for d in copy.deepcopy(newest_first(datasets, "created_at"))
        ]


SCHEMA = """
CREATE TABLE IF NOT EXISTS arena_runs (
    id TEXT PRIMARY KEY,
    level TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS arena_runs_level ON arena_runs(level, started_at DESC);
CREATE TABLE IF NOT EXISTS arena_cases (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    team TEXT NOT NULL,
    idx INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    data JSONB NOT NULL,
    events JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS arena_cases_run ON arena_cases(run_id);
CREATE TABLE IF NOT EXISTS arena_datasets (
    id TEXT PRIMARY KEY,
    level TEXT NOT NULL,
    team TEXT NOT NULL,
    created_at TEXT NOT NULL,
    data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS arena_datasets_level ON arena_datasets(level, created_at DESC);
"""


def encode_case(case: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    """Split a case into its document and its events (stored in their own column)."""
    data = {key: value for key, value in case.items() if key != "events"}
    return data, case.get("events", [])


def decode_case(data: str, events: str | None = None) -> dict[str, Any]:
    case = json.loads(data)
    if events is not None:
        case["events"] = json.loads(events)
    return case


class PostgresRunStore:
    """JSONB documents in Postgres via asyncpg, one connection per operation like `challenge_store`."""

    def __init__(self) -> None:
        self._schema_ready = False

    @asynccontextmanager
    async def connection(self):
        settings = get_settings()
        conn = await asyncpg.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            database=settings.DB_NAME,
        )
        try:
            if not self._schema_ready:
                async with conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock(79202604)")
                    await conn.execute(SCHEMA)
                self._schema_ready = True
            yield conn
        finally:
            await conn.close()

    async def setup(self) -> int:
        async with self.connection() as conn, conn.transaction():
            rows = await conn.fetch(
                "SELECT data FROM arena_runs WHERE status = ANY($1::text[]) FOR UPDATE",
                sorted(ACTIVE_STATUSES),
            )
            for row in rows:
                await self._save_run(conn, interrupt(json.loads(row["data"])))
        return len(rows)

    async def _save_run(self, conn, run: dict[str, Any]) -> None:
        await conn.execute(
            "INSERT INTO arena_runs(id,level,status,started_at,data) VALUES($1,$2,$3,$4,$5::jsonb) "
            "ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,data=EXCLUDED.data,updated_at=now()",
            run["id"],
            run["level"],
            run["status"],
            run["started_at"],
            json.dumps(run),
        )

    async def save_run(self, run: dict[str, Any]) -> None:
        async with self.connection() as conn:
            await self._save_run(conn, run)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            row = await conn.fetchrow("SELECT data FROM arena_runs WHERE id=$1", run_id)
        return json.loads(row["data"]) if row else None

    async def list_runs(self, level: str | None = None) -> list[dict[str, Any]]:
        async with self.connection() as conn:
            rows = await conn.fetch(
                "SELECT data FROM arena_runs WHERE $1::text IS NULL OR level=$1 ORDER BY started_at DESC",
                level,
            )
        return [json.loads(row["data"]) for row in rows]

    async def save_case(self, case: dict[str, Any]) -> None:
        data, events = encode_case(case)
        async with self.connection() as conn:
            await conn.execute(
                "INSERT INTO arena_cases(id,run_id,team,idx,data,events) VALUES($1,$2,$3,$4,$5::jsonb,$6::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data,events=EXCLUDED.events,updated_at=now()",
                case["id"],
                case["run_id"],
                case["team"],
                case["index"],
                json.dumps(data),
                json.dumps(events),
            )

    async def list_cases(
        self, run_id: str, *, events: bool = False
    ) -> list[dict[str, Any]]:
        columns = "data, events" if events else "data"
        async with self.connection() as conn:
            rows = await conn.fetch(
                f"SELECT {columns} FROM arena_cases WHERE run_id=$1 "
                "ORDER BY CASE WHEN team='red' THEN 0 ELSE 1 END, idx",
                run_id,
            )
        return [
            decode_case(row["data"], row["events"] if events else None) for row in rows
        ]

    async def get_case(self, run_id: str, case_id: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                "SELECT data, events FROM arena_cases WHERE run_id=$1 AND id=$2",
                run_id,
                case_id,
            )
        return decode_case(row["data"], row["events"]) if row else None

    async def save_dataset(self, dataset: dict[str, Any]) -> None:
        async with self.connection() as conn:
            await conn.execute(
                "INSERT INTO arena_datasets(id,level,team,created_at,data) VALUES($1,$2,$3,$4,$5::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data",
                dataset["id"],
                dataset["level"],
                dataset["team"],
                dataset["created_at"],
                json.dumps(dataset),
            )

    async def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM arena_datasets WHERE id=$1", dataset_id
            )
        return json.loads(row["data"]) if row else None

    async def list_datasets(self, level: str) -> list[dict[str, Any]]:
        async with self.connection() as conn:
            rows = await conn.fetch(
                "SELECT data - 'cases' AS data FROM arena_datasets WHERE level=$1 ORDER BY created_at DESC",
                level,
            )
        return [json.loads(row["data"]) for row in rows]


def create_store(kind: str | None = None) -> RunStore:
    """The store selected by `ARENA_STORE` (`postgres` by default, `memory` for runs without a database)."""
    kind = kind or get_settings().ARENA_STORE
    if kind == "memory":
        return MemoryRunStore()
    if kind == "postgres":
        return PostgresRunStore()
    raise ValueError(f"unknown ARENA_STORE '{kind}'")
