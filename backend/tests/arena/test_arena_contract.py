"""The JSON the arena API really returns, checked against the contract the frontend types encode.

`frontend/src/arena/types.ts` mirrors these key sets; a change here needs a change there (and in
IMPLEMENTATION.md §3–§5). Full fake runs (direct multi-turn and indirect single-turn, a reused dataset, a stopped
run with a live case) go through the API with the in-memory store and the fakes of `test_arena_runs`.

With `ARENA_CONTRACT_DUMP=<dir>` every response is also written to `<dir>/<name>.json`, e.g. to type-check it
against the TypeScript types.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.arena.test_arena_runs import (
    Harness,
    by_label,
    engine_models,
    mixed_attack_catalog,
    until,
)

LEVEL_SUMMARY = {"id", "title", "description", "channel", "turns", "difficulties"}
LEVEL_DETAIL = {
    "level",
    "difficulty",
    "difficulties",
    "title",
    "description",
    "surface",
    "graph",
    "models",
    "source",
    "red_team",
    "green_team",
}
GRAPH = {"surface", "nodes", "edges"}
NODE = {"id", "kind", "label", "subtitle", "group", "details"}
NODE_KINDS = {
    "attacker",
    "user_task",
    "injection",
    "guardrail_group",
    "guardrail",
    "agent",
    "tool_group",
    "tool",
    "data",
    "success",
    "output",
}
EDGE = {"id", "source", "target", "kind", "label"}
ROLE_MODEL = {"role", "node", "model", "endpoint", "params"}
MODEL_SPEC = {"model", "endpoint", "params"}
RED_TEAM = {
    "attacker",
    "simulator",
    "evaluator",
    "objective",
    "attacks",
    "cases",
    "max_turns",
}
ATTACK = {"type", "title", "turns", "attempts"}
GREEN_TEAM = {"generator", "cases", "guidance"}

RUN_SUMMARY = {
    "id",
    "level",
    "difficulty",
    "status",
    "started_at",
    "finished_at",
    "elapsed",
    "progress",
    "counts",
    "error",
    "generation",
}
RUN_DETAIL = RUN_SUMMARY | {
    "title",
    "graph",
    "models",
    "datasets",
    "sources",
    "cases",
    "live",
}
COUNTS = {"succeeded", "defended", "false_positive", "passed", "error"}
CASE_SUMMARY = {
    "id",
    "index",
    "label",
    "team",
    "attack_type",
    "outcome",
    "decided_by",
    "decided_label",
    "turns",
    "duration",
    "input_preview",
    "started_at",
    "status",
    "error",
}
CASE_DETAIL = CASE_SUMMARY | {
    "input",
    "content",
    "messages",
    "success",
    "usage",
    "events",
    "graph",
}
EVENT = {"seq", "t", "at", "turn", "kind", "node", "edge", "data"}
RESULTS = {
    "rates",
    "matrix",
    "errors",
    "counts",
    "by_attack_type",
    "by_decision",
    "cases",
}
BY_ATTACK_TYPE = {
    "type",
    "title",
    "turns",
    "attempts",
    "succeeded",
    "defended",
    "errors",
}
BY_DECISION = {"decided_by", "label", "red", "green"}
DATASET_SUMMARY = {
    "id",
    "name",
    "level",
    "team",
    "created_at",
    "size",
    "run_id",
    "difficulty",
    "models",
}
EXPORT = {"run", "models", "datasets", "snapshot", "results", "cases"}

GUARDRAIL_DATA = {
    "guardrail_id",
    "guardrail_kind",
    "guardrail_name",
    "position",
    "model",
    "check_id",
    "status",
    "rule",
    "reason",
    "changed",
}
# Event `data` per kind: (required keys, optional keys). `injection` carries exactly the injection fields.
EVENT_DATA: dict[str, tuple[set[str], set[str]]] = {
    "attacker_message": ({"text", "team"}, set()),
    "user_task": ({"text"}, set()),
    "guardrail": (GUARDRAIL_DATA, {"failed"}),
    "model_call": ({"model", "n"}, set()),
    "tool_call": ({"tool", "call_id", "args"}, set()),
    "tool_result": ({"tool", "call_id", "result", "blocked_by"}, set()),
    "agent_reply": ({"text"}, set()),
    "success_check": ({"met", "kind", "detail"}, set()),
    "limit": ({"limit", "message"}, set()),
    "error": ({"message"}, set()),
}
DECIDED_BY_KINDS = {
    "guardrail",
    "success_check",
    "agent",
    "turn_limit",
    "limit",
    "error",
}


def keys(value: dict[str, Any], expected: set[str], where: str) -> None:
    assert set(value) == expected, (
        f"{where}: extra {sorted(set(value) - expected)}, missing {sorted(expected - set(value))}"
    )


def check_graph(graph: dict[str, Any]) -> None:
    keys(graph, GRAPH, "graph")
    for node in graph["nodes"]:
        keys(node, NODE, f"node {node['id']}")
        assert node["kind"] in NODE_KINDS
        keys(node["details"], {"settings", "prompts", "model"}, "node.details")
        if node["details"]["model"] is not None:
            keys(node["details"]["model"], MODEL_SPEC, "node.details.model")
    for edge in graph["edges"]:
        keys(edge, EDGE, f"edge {edge['id']}")


def check_case_summary(case: dict[str, Any]) -> None:
    keys(case, CASE_SUMMARY, f"case {case['label']}")
    if case["decided_by"] is not None:
        assert case["decided_by"]["kind"] in DECIDED_BY_KINDS
        assert case["decided_label"]


def check_event(event: dict[str, Any], injection_fields: set[str]) -> None:
    keys(event, EVENT, "event")
    data = event["data"]
    if event["kind"] == "injection":
        keys(data, injection_fields, "injection data")
        return
    required, optional = EVENT_DATA[event["kind"]]
    assert required <= set(data) <= required | optional, (event["kind"], set(data))


def check_run_detail(run: dict[str, Any]) -> None:
    keys(run, RUN_DETAIL, "run detail")
    keys(run["counts"], COUNTS, "counts")
    check_graph(run["graph"])
    for model in run["models"]:
        keys(model, ROLE_MODEL, f"model {model['role']}")
    for case in run["cases"]:
        check_case_summary(case)
    for team in ("red", "green"):
        keys(run["generation"][team], {"status", "error"}, "generation")
        keys(run["progress"][team], {"done", "total"}, "progress")
    if run["live"] is not None:
        keys(run["live"], {"case_id", "events"}, "live")


def check_results(results: dict[str, Any]) -> None:
    keys(results, RESULTS, "results")
    for rate in results["rates"].values():
        keys(rate, {"value", "numerator", "denominator"}, "rate")
    for row in results["by_attack_type"]:
        keys(row, BY_ATTACK_TYPE, "by_attack_type")
    for row in results["by_decision"]:
        keys(row, BY_DECISION, "by_decision")
        assert row["label"]


class Dump:
    def __init__(self) -> None:
        target = os.environ.get("ARENA_CONTRACT_DUMP")
        self.dir = Path(target) if target else None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    def __call__(self, name: str, value: Any) -> Any:
        if self.dir:
            (self.dir / f"{name}.json").write_text(json.dumps(value, indent=2))
        return value


async def full_run(
    harness: Harness, dump: Dump, prefix: str, level: str, injection: set[str]
) -> dict[str, Any]:
    started = dump(f"{prefix}-create", await harness.start(level, "hard"))
    keys(started, RUN_SUMMARY, "POST /runs")
    await asyncio.wait_for(harness.runs.wait(started["id"]), 30)
    run = dump(f"{prefix}-run", await harness.get(f"/runs/{started['id']}"))
    check_run_detail(run)
    assert run["status"] == "completed"
    results = dump(f"{prefix}-results", await harness.get(f"/runs/{run['id']}/results"))
    check_results(results)
    for case in run["cases"]:
        detail = await harness.get(f"/runs/{run['id']}/cases/{case['id']}")
        keys(detail, CASE_DETAIL, f"case detail {case['label']}")
        check_graph(detail["graph"])
        for event in detail["events"]:
            check_event(event, injection)
        if case["label"] in ("01", "G01"):
            dump(f"{prefix}-case-{case['label']}", detail)
    exported = dump(f"{prefix}-export", await harness.get(f"/runs/{run['id']}/export"))
    keys(exported, EXPORT, "export")
    keys(exported["run"], RUN_DETAIL, "export.run")
    keys(exported["snapshot"], {"level", "graph"}, "export.snapshot")
    for case in exported["cases"]:
        keys(case, CASE_DETAIL - {"graph"}, "export case")
    return run


@pytest.mark.asyncio
async def test_api_responses_match_the_frontend_contract():
    dump = Dump()
    harness = Harness(catalog=mixed_attack_catalog())

    levels = dump("levels", await harness.get("/levels"))
    for level in levels:
        keys(level, LEVEL_SUMMARY, f"level {level['id']}")
    for level_id in ("hidden-mailbox", "email-review"):
        detail = dump(
            f"level-{level_id}-hard", await harness.get(f"/levels/{level_id}/hard")
        )
        keys(detail, LEVEL_DETAIL, "level detail")
        check_graph(detail["graph"])
        keys(detail["red_team"], RED_TEAM, "red_team")
        for attack in detail["red_team"]["attacks"]:
            keys(attack, ATTACK, "attack")
        keys(detail["green_team"], GREEN_TEAM, "green_team")
        keys(detail["green_team"]["guidance"], {"file", "text"}, "guidance")
        for role in ("attacker", "simulator", "evaluator"):
            keys(detail["red_team"][role], MODEL_SPEC, role)
        for model in detail["models"]:
            keys(model, ROLE_MODEL, f"model {model['role']}")
        for file in detail["source"]["files"]:
            keys(file, {"path", "content"}, "source file")

    direct = await full_run(harness, dump, "direct", "hidden-mailbox", set())
    cases = by_label(direct["cases"])
    assert {case["decided_by"]["kind"] for case in direct["cases"]} >= {
        "guardrail",
        "success_check",
        "agent",
    }
    assert cases["G02"]["outcome"] == "false_positive"
    assert cases["G02"]["decided_label"] == "Input judge · UNSAFE"
    indirect = await full_run(
        harness, dump, "indirect", "email-review", {"subject", "body"}
    )

    datasets = dump("datasets", await harness.get("/levels/email-review/datasets"))
    assert {dataset["team"] for dataset in datasets} == {"red", "green"}
    for dataset in datasets:
        keys(dataset, DATASET_SUMMARY, "dataset summary")
    red = next(d for d in datasets if d["team"] == "red")
    dataset = dump("dataset-red", await harness.get(f"/datasets/{red['id']}"))
    keys(dataset, DATASET_SUMMARY | {"cases"}, "dataset detail")
    for case in dataset["cases"]:
        assert (
            {"id", "team", "attack_type"}
            <= set(case)
            <= {
                "id",
                "team",
                "attack_type",
                "input",
                "content",
            }
        )
    assert red["run_id"] == indirect["id"]

    listed = dump("runs", await harness.get("/runs"))
    for run in listed:
        keys(run, RUN_SUMMARY, "GET /runs")


@pytest.mark.asyncio
async def test_live_and_stopped_run_match_the_frontend_contract():
    dump = Dump()
    gate = {"<body 2>": asyncio.Event()}
    harness = Harness(models=engine_models(gate))
    summary = await harness.start("email-review", "hard")
    run_id = summary["id"]

    async def live():
        run = await harness.get(f"/runs/{run_id}")
        return bool(run["live"] and run["live"]["events"])

    await until(live)
    run = dump("live-run", await harness.get(f"/runs/{run_id}"))
    check_run_detail(run)
    for event in run["live"]["events"]:
        check_event(event, {"subject", "body"})
    stopped = dump(
        "stopped-summary",
        (await harness.http.post(f"/api/v1/arena/runs/{run_id}/stop")).json(),
    )
    keys(stopped, RUN_SUMMARY, "POST /runs/{id}/stop")
    run = dump("stopped-run", await harness.get(f"/runs/{run_id}"))
    check_run_detail(run)
    assert run["status"] == "stopped"
