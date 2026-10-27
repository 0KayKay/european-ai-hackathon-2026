"""Run results from recorded data only (R-RES-1..R-RES-7, IMPLEMENTATION.md §5).

Everything here is a pure function of the stored run (its snapshot) and its cases, so results never depend on
later config edits and never contain generated interpretation.
"""

import json
from typing import Any

from hackathon.arena.registry.attacks import ATTACK_TYPES
from hackathon.arena.registry.guardrails import GUARDRAIL_KINDS

OUTCOMES = ("succeeded", "defended", "false_positive", "passed", "error")

LIMIT_LABELS = {
    "run.model_requests": ("Run limit", "model requests"),
    "run.tool_calls": ("Run limit", "tool calls"),
    "conversation.model_requests": ("Conversation limit", "model requests"),
    "conversation.tool_calls": ("Conversation limit", "tool calls"),
}

PREVIEW_LENGTH = 160

DECISION_ORDER = {
    "guardrail": 0,
    "agent": 1,
    "success_check": 2,
    "limit": 3,
    "turn_limit": 4,
    "error": 5,
}


def _level(run: dict[str, Any]) -> dict[str, Any]:
    return run["snapshot"]["level"]


def _guardrail_labels(run: dict[str, Any]) -> dict[str, str]:
    nodes = {node["id"]: node for node in run["snapshot"]["graph"]["nodes"]}
    labels = {}
    for guardrail in _level(run)["guardrails"]:
        node = nodes.get(f"guardrail:{guardrail['id']}")
        labels[guardrail["id"]] = node["label"] if node else guardrail["id"]
    return labels


def decision_label(
    run: dict[str, Any], decided_by: dict[str, Any] | None
) -> str | None:
    """Display label of a deciding step, from the run's config snapshot."""
    if not decided_by:
        return None
    kind = decided_by.get("kind")
    if kind == "guardrail":
        guardrail_id = decided_by.get("guardrail_id", "")
        return f"Blocked by {_guardrail_labels(run).get(guardrail_id, guardrail_id)}"
    if kind == "success_check":
        return "Success check met"
    if kind == "agent":
        return "Passed all guardrails, success check not met"
    if kind == "turn_limit":
        return f"Turn limit reached ({_level(run)['surface']['max_turns']} turns)"
    if kind == "limit":
        limit = decided_by.get("limit", "")
        if limit in LIMIT_LABELS:
            title, unit = LIMIT_LABELS[limit]
            section, name = limit.split(".")
            value = _level(run)["limits"][section][name]
            return f"{title} reached ({value} {unit})"
        return f"Limit reached ({limit})"
    if kind == "error":
        return "Error"
    return str(kind)


def case_label(team: str, index: int) -> str:
    return f"{index:02d}" if team == "red" else f"G{index:02d}"


def input_preview(case: dict[str, Any]) -> str | None:
    if case.get("input"):
        text = case["input"]
    elif case.get("content"):
        text = " · ".join(str(value) for value in case["content"].values())
    elif case.get("messages"):
        text = case["messages"][0]
    else:
        return None
    return text if len(text) <= PREVIEW_LENGTH else text[: PREVIEW_LENGTH - 1] + "…"


def case_summary(run: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": case["id"],
        "index": case["index"],
        "label": case_label(case["team"], case["index"]),
        "team": case["team"],
        "attack_type": case.get("attack_type"),
        "outcome": case.get("outcome"),
        "decided_by": case.get("decided_by"),
        "decided_label": decision_label(run, case.get("decided_by")),
        "turns": case.get("turns", 0),
        "duration": case.get("duration"),
        "input_preview": input_preview(case),
        "started_at": case.get("started_at"),
        "status": case.get("status", "pending"),
        "error": case.get("error"),
    }


def counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    values = dict.fromkeys(OUTCOMES, 0)
    for case in cases:
        if case.get("outcome") in values:
            values[case["outcome"]] += 1
    return values


def progress(
    run: dict[str, Any], cases: list[dict[str, Any]]
) -> dict[str, dict[str, int]]:
    """Done = decided cases; total = planned cases (known before generation has finished)."""
    result = {}
    for team in ("red", "green"):
        team_cases = [case for case in cases if case["team"] == team]
        done = sum(1 for case in team_cases if case.get("outcome"))
        total = max(run.get("plan", {}).get(team, 0), len(team_cases))
        result[team] = {"done": done, "total": total}
    return result


def rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def _decision_key(decided_by: dict[str, Any]) -> str:
    return json.dumps(decided_by, sort_keys=True)


def by_decision(
    run: dict[str, Any], decided: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Where cases were decided: blocks per guardrail, passed all guardrails, success met, limits, errors."""
    rows: dict[str, dict[str, Any]] = {}

    def row(decided_by: dict[str, Any]) -> dict[str, Any]:
        key = _decision_key(decided_by)
        if key not in rows:
            rows[key] = {
                "decided_by": decided_by,
                "label": decision_label(run, decided_by),
                "red": 0,
                "green": 0,
            }
        return rows[key]

    for guardrail in _level(run)["guardrails"]:
        kind = GUARDRAIL_KINDS.get(guardrail["kind"])
        if kind is None or kind.uses_model:  # sanitizers never block
            row({"kind": "guardrail", "guardrail_id": guardrail["id"]})
    row({"kind": "agent"})
    row({"kind": "success_check"})
    for case in decided:
        row(case["decided_by"] or {"kind": "error"})[case["team"]] += 1
    # Stable sort: guardrails keep their config order, limits their first occurrence.
    return sorted(
        rows.values(),
        key=lambda item: DECISION_ORDER.get(item["decided_by"].get("kind"), 6),
    )


def by_attack_type(
    run: dict[str, Any], red: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    configured = [attack["type"] for attack in _level(run)["red_team"]["attacks"]]
    types = list(
        dict.fromkeys(
            configured + [case.get("attack_type") or "unknown" for case in red]
        )
    )
    result = []
    for type_ in types:
        cases = [
            case for case in red if (case.get("attack_type") or "unknown") == type_
        ]
        if not cases and type_ not in configured:
            continue
        attack = ATTACK_TYPES.get(type_)
        result.append(
            {
                "type": type_,
                "title": attack.title if attack else type_,
                "turns": attack.turns if attack else None,
                "attempts": len(cases),
                "succeeded": sum(1 for case in cases if case["outcome"] == "succeeded"),
                "defended": sum(1 for case in cases if case["outcome"] == "defended"),
                "errors": sum(1 for case in cases if case["outcome"] == "error"),
            }
        )
    return result


def results(run: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    """`GET /runs/{id}/results`: only decided cases count; errors are excluded from both rates (R-RES-1)."""
    decided = [case for case in cases if case.get("outcome")]
    red = [case for case in decided if case["team"] == "red"]
    green = [case for case in decided if case["team"] == "green"]
    total = counts(decided)
    return {
        "rates": {
            "attack_success": rate(
                total["succeeded"],
                sum(1 for case in red if case["outcome"] != "error"),
            ),
            "false_positive": rate(
                total["false_positive"],
                sum(1 for case in green if case["outcome"] != "error"),
            ),
        },
        "matrix": {
            "red": {"stopped": total["defended"], "got_through": total["succeeded"]},
            "green": {"blocked": total["false_positive"], "passed": total["passed"]},
        },
        "errors": {
            "red": sum(1 for case in red if case["outcome"] == "error"),
            "green": sum(1 for case in green if case["outcome"] == "error"),
        },
        "counts": total,
        "by_attack_type": by_attack_type(run, red),
        "by_decision": by_decision(run, decided),
        "cases": [case_summary(run, case) for case in cases],
    }
