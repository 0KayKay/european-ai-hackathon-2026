"""The run stores behave alike: in memory always, against Postgres when `ARENA_TEST_DATABASE_URL` is set.

    ARENA_TEST_DATABASE_URL=postgresql://app:devpassword@localhost:5432/app uv run pytest tests/arena/test_arena_store.py

The Postgres tests write rows with random ids into the arena tables of that database; use a throwaway database.
"""

import asyncio
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from hackathon.arena.runs.store import MemoryRunStore, PostgresRunStore
from tests.arena.test_arena_runs import Harness, by_label

DATABASE_URL = os.environ.get("ARENA_TEST_DATABASE_URL")
postgres = pytest.mark.skipif(
    not DATABASE_URL, reason="set ARENA_TEST_DATABASE_URL to test the Postgres store"
)


@pytest.fixture
def database(monkeypatch):
    url = urlparse(DATABASE_URL)
    for key, value in {
        "DB_HOST": url.hostname or "localhost",
        "DB_PORT": str(url.port or 5432),
        "DB_USER": url.username or "app",
        "DB_PASSWORD": url.password or "",
        "DB_NAME": url.path.lstrip("/") or "app",
    }.items():
        monkeypatch.setenv(key, value)
    from hackathon.config import get_settings

    get_settings.cache_clear()


def run_record(status: str = "running") -> dict:
    return {
        "id": str(uuid4()),
        "level": f"test-level-{uuid4().hex[:8]}",
        "status": status,
        "started_at": "2026-10-27T20:00:00.000Z",
        "finished_at": None,
        "progress": {"red": {"done": 0, "total": 1}},
        "snapshot": {"level": {"title": "T · ü"}, "graph": {"nodes": []}},
    }


def case_record(run_id: str, team: str, index: int, status: str = "done") -> dict:
    return {
        "id": str(uuid4()),
        "run_id": run_id,
        "team": team,
        "index": index,
        "status": status,
        "input": "<attack 1> ✓",
        "events": [{"seq": 0, "kind": "attacker_message", "data": {"text": "ü"}}],
    }


async def check_store(store) -> None:
    run = run_record()
    await store.save_run(run)
    await store.save_run({**run, "progress": {"red": {"done": 1, "total": 1}}})
    stored = await store.get_run(run["id"])
    assert stored["snapshot"] == run["snapshot"]  # kept although saved once
    assert stored["progress"]["red"]["done"] == 1
    listed = await store.list_runs(run["level"])
    assert [r["id"] for r in listed] == [run["id"]]

    cases = [
        case_record(run["id"], "green", 1),
        case_record(run["id"], "red", 2, status="running"),
        case_record(run["id"], "red", 1),
    ]
    for case in cases:
        await store.save_case(case)
    ordered = await store.list_cases(run["id"])
    assert [(c["team"], c["index"]) for c in ordered] == [
        ("red", 1),
        ("red", 2),
        ("green", 1),
    ]
    assert "events" not in ordered[0]
    with_events = await store.list_cases(run["id"], events=True)
    assert with_events[0]["events"][0]["data"]["text"] == "ü"
    assert (await store.get_case(run["id"], cases[0]["id"]))["input"] == "<attack 1> ✓"
    assert await store.get_case("other-run", cases[0]["id"]) is None

    dataset = {
        "id": str(uuid4()),
        "level": run["level"],
        "team": "red",
        "created_at": "2026-10-27T20:00:01.000Z",
        "size": 1,
        "cases": [{"id": "c1", "team": "red", "content": {"body": "<body 1>"}}],
    }
    await store.save_dataset(dataset)
    assert (await store.get_dataset(dataset["id"]))["cases"] == dataset["cases"]
    assert await store.list_datasets(run["level"]) == [
        {k: v for k, v in dataset.items() if k != "cases"}
    ]

    # A restart: the active run becomes interrupted, its running case stopped.
    assert await store.setup() >= 1
    interrupted = await store.get_run(run["id"])
    assert interrupted["status"] == "interrupted" and interrupted["finished_at"]
    assert interrupted["snapshot"] == run["snapshot"]
    statuses = {c["index"]: c["status"] for c in await store.list_cases(run["id"])}
    assert statuses == {1: "done", 2: "stopped"}
    await store.close()


@pytest.mark.asyncio
async def test_memory_store():
    await check_store(MemoryRunStore())


@postgres
@pytest.mark.asyncio
async def test_postgres_store(database):
    await check_store(PostgresRunStore())


@postgres
@pytest.mark.asyncio
async def test_postgres_store_full_run(database):
    store = PostgresRunStore()
    harness = Harness(store=store)
    run = await harness.run("email-review", "hard")
    assert run["status"] == "completed" and len(run["cases"]) == 20
    # Everything is read back from Postgres once the run is no longer active.
    reloaded = await harness.get(f"/runs/{run['id']}")
    assert reloaded["cases"] == run["cases"]
    case = by_label(run["cases"])["01"]
    detail = await harness.get(f"/runs/{run['id']}/cases/{case['id']}")
    assert detail["events"] and detail["graph"]["nodes"]
    exported = await harness.get(f"/runs/{run['id']}/export")
    assert exported["datasets"]["red"]["size"] == 10
    reused = await harness.run(
        "email-review",
        "easy",
        red={"source": "dataset", "dataset_id": run["datasets"]["red"]},
    )
    assert [c["input_preview"] for c in reused["cases"] if c["team"] == "red"] == [
        c["input_preview"] for c in run["cases"] if c["team"] == "red"
    ]
    await asyncio.wait_for(store.close(), 10)
