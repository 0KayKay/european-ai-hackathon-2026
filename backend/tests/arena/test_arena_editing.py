"""Level files edited in the UI: overrides on top of `backend/levels/` (PUT/DELETE /levels/{level}/source).

Runs against the in-memory store; the store round trip also runs against Postgres when `ARENA_TEST_DATABASE_URL`
is set (see `test_arena_store.py`).
"""

import asyncio

import pytest

from hackathon.arena.catalog import LevelCatalog
from hackathon.arena.config.loader import LEVELS_ROOT, load_levels
from hackathon.arena.runs.store import MemoryRunStore, PostgresRunStore
from tests.arena.test_arena_runs import Harness
from tests.arena.test_arena_store import database, postgres  # noqa: F401

LEVEL = "hidden-mailbox"
LEVEL_YAML = f"{LEVEL}/level.yaml"
SYSTEM_PROMPT = f"{LEVEL}/prompts/system.md"
REPO_YAML = (LEVELS_ROOT / LEVEL_YAML).read_text()
INPUT_JUDGE_AT = """        at: "user->agent"
        description: Blocks user messages"""
MOVED_YAML = REPO_YAML.replace(
    INPUT_JUDGE_AT,
    INPUT_JUDGE_AT.replace("user->agent", "agent->output"),
)


def editing_harness(store=None) -> Harness:
    store = store or MemoryRunStore()
    return Harness(store=store, catalog=LevelCatalog.build(load_levels(), store=store))


async def put(harness: Harness, files: dict[str, str], base_version: int = 0, **body):
    return await harness.http.put(
        f"/api/v1/arena/levels/{LEVEL}/source",
        json={
            "files": [{"path": path, "content": c} for path, c in files.items()],
            "base_version": base_version,
            "difficulty": "hard",
            **body,
        },
    )


def group_of(detail: dict, node_id: str) -> str | None:
    return next(n["group"] for n in detail["graph"]["nodes"] if n["id"] == node_id)


def line_of(text: str, needle: str) -> int:
    return next(n for n, line in enumerate(text.splitlines(), 1) if needle in line)


def test_moved_guardrail_fixture_changes_the_file():
    assert REPO_YAML.count(INPUT_JUDGE_AT) == 1 and MOVED_YAML != REPO_YAML


@pytest.mark.asyncio
async def test_valid_edit_moves_the_guardrail_node():
    harness = editing_harness()
    before = await harness.get(f"/levels/{LEVEL}/hard")
    assert (before["version"], before["modified"], before["override_error"]) == (
        0,
        False,
        None,
    )
    assert group_of(before, "guardrail:input-judge") == "guardrails:user->agent"
    editable = {f["path"]: f["editable"] for f in before["source"]["files"]}
    assert editable[LEVEL_YAML] and editable[f"{LEVEL}/prompts/input_judge.md"]
    assert not editable["shared/prompts/judge_system.md"]

    response = await put(harness, {LEVEL_YAML: MOVED_YAML})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert (saved["level"], saved["difficulty"], saved["version"]) == (LEVEL, "hard", 1)
    assert group_of(saved, "guardrail:input-judge") == "guardrails:agent->output"
    modified = {f["path"]: f["modified"] for f in saved["source"]["files"]}
    assert modified[LEVEL_YAML] and not modified[SYSTEM_PROMPT]
    assert saved["source"]["files"][0] == {
        "path": LEVEL_YAML,
        "content": MOVED_YAML,
        "modified": True,
        "editable": True,
    }

    # Every following request serves the saved version; other levels are untouched.
    again = await harness.get(f"/levels/{LEVEL}/hard")
    assert group_of(again, "guardrail:input-judge") == "guardrails:agent->output"
    levels = {level["id"]: level for level in await harness.get("/levels")}
    assert (levels[LEVEL]["modified"], levels[LEVEL]["version"]) == (True, 1)
    assert not levels["email-review"]["modified"]
    source = await harness.get(f"/levels/{LEVEL}/source")
    assert source["version"] == 1 and source["updated_by"] == "tester"
    stored = next(f for f in source["files"] if f["path"] == LEVEL_YAML)
    assert (stored["modified"], stored["updated_by"], stored["version"]) == (
        True,
        "tester",
        1,
    )
    # Files of every difficulty are listed (input_judge.md is only used on hard).
    assert f"{LEVEL}/prompts/input_judge.md" in {f["path"] for f in source["files"]}
    # The repository file is unchanged.
    assert (LEVELS_ROOT / LEVEL_YAML).read_text() == REPO_YAML


@pytest.mark.asyncio
async def test_saving_the_repository_content_drops_the_override():
    harness = editing_harness()
    assert (await put(harness, {LEVEL_YAML: MOVED_YAML})).status_code == 200
    response = await put(harness, {LEVEL_YAML: REPO_YAML}, base_version=1)
    assert response.status_code == 200
    assert (response.json()["version"], response.json()["modified"]) == (2, False)
    assert harness.store.overrides[LEVEL]["files"] == {}


@pytest.mark.asyncio
async def test_invalid_yaml_is_rejected_with_its_line():
    harness = editing_harness()
    broken = REPO_YAML.replace("order: 1\n", "order: [1\n")
    response = await put(harness, {LEVEL_YAML: broken})
    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["path"] == LEVEL_YAML
    assert error["message"].startswith("invalid YAML:")
    assert (
        line_of(broken, "order: [1")
        <= error["line"]
        <= line_of(broken, "order: [1") + 2
    )
    # Nothing was stored.
    assert (await harness.get(f"/levels/{LEVEL}/hard"))["version"] == 0


@pytest.mark.asyncio
async def test_invalid_schema_is_rejected_with_its_line():
    harness = editing_harness()
    misplaced = MOVED_YAML.replace(
        'at: "agent->output"\n        description: Blocks',
        'at: "nowhere"\n        description: Blocks',
    )
    response = await put(harness, {LEVEL_YAML: misplaced})
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {
            "path": LEVEL_YAML,
            "message": "[hard] guardrails[1].at: unknown position 'nowhere' "
            "(use user->agent, agent->output, tool->agent or around:<tool>)",
            "line": line_of(misplaced, 'at: "nowhere"'),
        }
    ]

    typo = REPO_YAML.replace("order: 1\n", "order: 1\nordr: 2\n")
    response = await put(harness, {LEVEL_YAML: typo})
    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["message"] == "[easy] ordr: Extra inputs are not permitted"
    assert error["line"] == line_of(typo, "ordr: 2")


@pytest.mark.asyncio
async def test_unknown_placeholder_names_the_prompt_file_and_line():
    harness = editing_harness()
    prompt = (LEVELS_ROOT / SYSTEM_PROMPT).read_text() + "Contact: {{nope}}\n"
    response = await put(harness, {SYSTEM_PROMPT: prompt})
    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["path"] == SYSTEM_PROMPT
    assert error["message"].startswith("unknown placeholder {{nope}}")
    assert error["line"] == line_of(prompt, "{{nope}}")


@pytest.mark.asyncio
async def test_paths_outside_the_level_directory_are_rejected():
    harness = editing_harness()
    response = await put(
        harness,
        {
            f"{LEVEL}/../email-review/level.yaml": "x",
            "/etc/passwd": "x",
            "shared/prompts/refusal.md": "x",
            "email-review/level.yaml": "x",
            f"{LEVEL}//level.yaml": "x",
        },
    )
    assert response.status_code == 422
    errors = {e["path"]: e["message"] for e in response.json()["errors"]}
    assert errors == {
        f"{LEVEL}/../email-review/level.yaml": "'..' is not allowed in file paths",
        "/etc/passwd": "file paths must be relative to backend/levels/",
        "shared/prompts/refusal.md": "files in shared/ are read-only: they are used by every level",
        "email-review/level.yaml": f"only files inside {LEVEL}/ can be edited with this level",
        f"{LEVEL}//level.yaml": "file paths must not contain empty or '.' segments",
    }
    # A file in the level directory that the level does not use is rejected too.
    response = await put(harness, {f"{LEVEL}/prompts/unused.md": "text"})
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == f"{LEVEL}/prompts/unused.md"
    assert "does not use this file" in response.json()["errors"][0]["message"]
    assert (await harness.get(f"/levels/{LEVEL}/hard"))["version"] == 0


@pytest.mark.asyncio
async def test_a_new_file_needs_to_be_in_the_same_save():
    harness = editing_harness()
    yaml = REPO_YAML.replace(
        "policy: prompts/input_judge.md", "policy: prompts/strict_judge.md"
    )
    response = await put(harness, {LEVEL_YAML: yaml})
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {
            "path": LEVEL_YAML,
            "message": "[hard] guardrails[1].policy: file not found: prompts/strict_judge.md",
            "line": line_of(yaml, "policy: prompts/strict_judge.md"),
        }
    ]
    new = f"{LEVEL}/prompts/strict_judge.md"
    response = await put(
        harness, {LEVEL_YAML: yaml, new: "Check {{content}} strictly.\n"}
    )
    assert response.status_code == 200, response.text
    files = {f["path"]: f for f in response.json()["source"]["files"]}
    assert (
        files[new]["modified"]
        and files[new]["content"] == "Check {{content}} strictly.\n"
    )
    judge = next(
        n
        for n in response.json()["graph"]["nodes"]
        if n["id"] == "guardrail:input-judge"
    )
    assert any(p["file"] == new for p in judge["details"]["prompts"])


@pytest.mark.asyncio
async def test_a_stale_version_conflicts():
    harness = editing_harness()
    assert (await put(harness, {LEVEL_YAML: MOVED_YAML})).status_code == 200
    response = await put(harness, {LEVEL_YAML: REPO_YAML}, base_version=0)
    assert response.status_code == 409
    body = response.json()
    assert body["version"] == 1
    assert body["current"]["version"] == 1
    assert body["current"]["source"]["files"][0]["content"] == MOVED_YAML
    assert "Someone else saved" in body["detail"]
    stale_reset = await harness.http.delete(
        f"/api/v1/arena/levels/{LEVEL}/source", params={"base_version": 0}
    )
    assert stale_reset.status_code == 409


@pytest.mark.asyncio
async def test_reset_returns_to_the_repository_version():
    harness = editing_harness()
    assert (await put(harness, {LEVEL_YAML: MOVED_YAML})).status_code == 200
    response = await harness.http.delete(
        f"/api/v1/arena/levels/{LEVEL}/source",
        params={"difficulty": "hard", "base_version": 1},
    )
    assert response.status_code == 200
    detail = response.json()
    assert (detail["version"], detail["modified"]) == (2, False)
    assert group_of(detail, "guardrail:input-judge") == "guardrails:user->agent"
    assert detail["source"]["files"][0]["content"] == REPO_YAML
    # The version keeps growing, so an editor that loaded version 1 conflicts.
    assert (
        await put(harness, {LEVEL_YAML: MOVED_YAML}, base_version=1)
    ).status_code == 409
    assert (
        await put(harness, {LEVEL_YAML: MOVED_YAML}, base_version=2)
    ).status_code == 200


@pytest.mark.asyncio
async def test_unknown_level_and_missing_store():
    harness = editing_harness()
    response = await harness.http.put(
        "/api/v1/arena/levels/unknown/source", json={"files": [], "base_version": 0}
    )
    assert response.status_code == 404
    plain = Harness()  # a catalog without a store serves the repository files only
    response = await plain.http.put(
        f"/api/v1/arena/levels/{LEVEL}/source", json={"files": [], "base_version": 0}
    )
    assert response.status_code == 503


async def check_overrides_survive_a_reload(store_factory) -> None:
    store = store_factory()
    first = editing_harness(store)
    version = (await first.get(f"/levels/{LEVEL}/hard"))["version"]
    response = await put(first, {LEVEL_YAML: MOVED_YAML}, base_version=version)
    assert response.status_code == 200, response.text

    # Another process (a restart, or a second replica) over the same database.
    second = editing_harness(store_factory())
    detail = await second.get(f"/levels/{LEVEL}/hard")
    assert detail["version"] == version + 1 and detail["modified"]
    assert group_of(detail, "guardrail:input-judge") == "guardrails:agent->output"

    # A save on the second replica is seen by the first on its next request.
    response = await second.http.delete(f"/api/v1/arena/levels/{LEVEL}/source")
    assert response.status_code == 200
    detail = await first.get(f"/levels/{LEVEL}/hard")
    assert (detail["version"], detail["modified"]) == (version + 2, False)
    assert group_of(detail, "guardrail:input-judge") == "guardrails:user->agent"


@pytest.mark.asyncio
async def test_memory_overrides_survive_a_reload():
    store = MemoryRunStore()
    await check_overrides_survive_a_reload(lambda: store)


@postgres
@pytest.mark.asyncio
async def test_postgres_overrides_survive_a_reload(database):  # noqa: F811
    stores = []

    def factory():
        stores.append(PostgresRunStore())
        return stores[-1]

    await check_overrides_survive_a_reload(factory)
    record = await stores[0].get_level_overrides(LEVEL)
    assert record["files"] == {} and record["updated_by"] == "tester"
    for store in stores:
        await asyncio.wait_for(store.close(), 10)


@pytest.mark.asyncio
async def test_memory_store_keeps_unchanged_files():
    store = MemoryRunStore()
    first = await store.save_level_overrides(
        LEVEL, {"a": "1", "b": "2"}, base_version=0, user_id="u1"
    )
    second = await store.save_level_overrides(
        LEVEL, {"a": "1", "b": "3\x00"}, base_version=1, user_id="u2"
    )
    assert second["files"]["a"] == first["files"]["a"]
    assert second["files"]["b"]["content"] == "3�"
    assert (second["files"]["b"]["version"], second["updated_by"]) == (2, "u2")
    assert await store.level_override_versions() == {LEVEL: 2}


@pytest.mark.asyncio
async def test_a_run_started_after_a_save_uses_the_new_config():
    harness = editing_harness()
    before = await harness.run(LEVEL, "hard")
    assert group_of(before, "guardrail:input-judge") == "guardrails:user->agent"

    assert (await put(harness, {LEVEL_YAML: MOVED_YAML})).status_code == 200
    after = await harness.run(LEVEL, "hard")
    assert group_of(after, "guardrail:input-judge") == "guardrails:agent->output"
    run = await harness.store.get_run(after["id"])
    judge = next(
        g for g in run["snapshot"]["level"]["guardrails"] if g["id"] == "input-judge"
    )
    assert judge["at"] == "agent->output"

    # The earlier run and its replay keep the config they ran with.
    old = await harness.get(f"/runs/{before['id']}")
    assert group_of(old, "guardrail:input-judge") == "guardrails:user->agent"
    case = old["cases"][0]
    replay = await harness.get(f"/runs/{before['id']}/cases/{case['id']}")
    assert group_of(replay, "guardrail:input-judge") == "guardrails:user->agent"


@pytest.mark.asyncio
async def test_a_broken_override_falls_back_to_the_repository_version():
    store = MemoryRunStore()
    # Stored by an earlier version of the app; the schema no longer accepts it.
    broken = REPO_YAML.replace("order: 1\n", "order: 1\nretired_field: true\n")
    await store.save_level_overrides(
        LEVEL, {LEVEL_YAML: broken}, base_version=0, user_id="earlier"
    )
    catalog = LevelCatalog.build(load_levels(), store=store)
    await catalog.refresh()  # what the app does at startup; it must not raise
    assert catalog.levels[LEVEL] is catalog.repository[LEVEL]

    harness = Harness(store=store, catalog=catalog)
    detail = await harness.get(f"/levels/{LEVEL}/hard")
    assert (detail["version"], detail["modified"]) == (1, False)
    assert detail["source"]["files"][0]["content"] == REPO_YAML
    error = detail["override_error"]
    assert error["errors"] == [
        {
            "path": LEVEL_YAML,
            "message": "[easy] retired_field: Extra inputs are not permitted",
            "line": line_of(broken, "retired_field"),
        }
    ]
    assert error["files"] == [{"path": LEVEL_YAML, "content": broken}]
    levels = {level["id"]: level for level in await harness.get("/levels")}
    assert "retired_field" in levels[LEVEL]["override_error"]
    # Runs use the repository version meanwhile.
    run = await harness.run(LEVEL, "hard")
    assert group_of(run, "guardrail:input-judge") == "guardrails:user->agent"

    # A save replaces the broken overrides.
    response = await put(harness, {LEVEL_YAML: MOVED_YAML}, base_version=1)
    assert response.status_code == 200, response.text
    assert response.json()["override_error"] is None
    assert (
        group_of(response.json(), "guardrail:input-judge") == "guardrails:agent->output"
    )
