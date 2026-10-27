"""Arena HTTP API (`/api/v1/arena`): levels, datasets, runs, results, replay and export (IMPLEMENTATION.md §5).

`router` is session-protected like the rest of `/api/v1`. `internal_router` serves the multi-turn callback of the
deepteam service; it is authenticated by the per-run `x-arena-token` instead and must not be routed by the gateway.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from hackathon.api.auth import require_user
from hackathon.arena.config.loader import LevelSet, load_levels
from hackathon.arena.config.resolved import ResolvedLevel
from hackathon.arena.graph import Graph, build_graph
from hackathon.arena.registry.attacks import ATTACK_TYPES
from hackathon.arena.runs.green_team import GreenGenerator
from hackathon.arena.runs.orchestrator import Orchestrator, TeamSource
from hackathon.arena.runs.red_team import DeepTeamClient
from hackathon.arena.runs.store import create_store

# Routes with a fixed second segment under /levels/{level_id}/ (e.g. `datasets`) must be declared before
# `get_level`, which otherwise matches them as a difficulty.
router = APIRouter(prefix="/api/v1/arena", dependencies=[Depends(require_user)])
internal_router = APIRouter(prefix="/api/v1/arena/internal")


@dataclass
class LevelCatalog:
    """Levels loaded once, with their graphs."""

    levels: dict[str, LevelSet]
    graphs: dict[tuple[str, str], Graph]

    @classmethod
    def build(cls, levels: dict[str, LevelSet]) -> "LevelCatalog":
        graphs = {
            (level_id, difficulty): build_graph(variant)
            for level_id, level in levels.items()
            for difficulty, variant in level.variants.items()
        }
        return cls(levels=levels, graphs=graphs)

    def variant(self, level_id: str, difficulty: str) -> ResolvedLevel:
        level = self.levels.get(level_id)
        if level is None:
            raise HTTPException(404, f"Unknown level '{level_id}'")
        variant = level.variants.get(difficulty)
        if variant is None:
            raise HTTPException(
                404, f"Level '{level_id}' has no difficulty '{difficulty}'"
            )
        return variant


@lru_cache
def get_level_catalog() -> LevelCatalog:
    """Load `backend/levels/` once per process; an invalid config raises `LevelConfigError`."""
    return LevelCatalog.build(load_levels())


Catalog = Annotated[LevelCatalog, Depends(get_level_catalog)]


@lru_cache
def get_orchestrator() -> Orchestrator:
    """The process-wide run orchestrator with the store selected by `ARENA_STORE`."""
    return Orchestrator(
        get_level_catalog(), create_store(), DeepTeamClient(), GreenGenerator()
    )


Runs = Annotated[Orchestrator, Depends(get_orchestrator)]
User = Annotated[str, Depends(require_user)]


def red_team_summary(level: ResolvedLevel) -> dict[str, Any]:
    red = level.red_team
    return {
        "attacker": red.attacker.summary(),
        "simulator": red.simulator.summary(),
        "evaluator": red.evaluator.summary(),
        "objective": red.objective.model_dump(),
        "attacks": [
            {
                "type": attack.type,
                "title": ATTACK_TYPES[attack.type].title,
                "turns": ATTACK_TYPES[attack.type].turns,
                "attempts": attack.attempts,
            }
            for attack in red.attacks
        ],
        "cases": sum(attack.attempts for attack in red.attacks),
        "max_turns": level.surface.max_turns,
    }


def green_team_summary(level: ResolvedLevel) -> dict[str, Any]:
    green = level.green_team
    return {
        "generator": green.generator.summary(),
        "cases": green.cases,
        "guidance": {"file": green.guidance.file, "text": green.guidance.text},
    }


def level_view(level: ResolvedLevel, graph: Graph) -> dict[str, Any]:
    return {
        "level": level.id,
        "difficulty": level.difficulty,
        "difficulties": level.difficulties,
        "title": level.title,
        "description": level.description,
        "surface": level.surface.model_dump(),
        "graph": graph.model_dump(),
        "models": level.model_roles(),
        "source": {
            "files": [
                {"path": path, "content": content}
                for path, content in level.sources.items()
            ]
        },
        "red_team": red_team_summary(level),
        "green_team": green_team_summary(level),
    }


@router.get("/levels")
async def list_levels(catalog: Catalog) -> list[dict[str, Any]]:
    return [level.summary() for level in catalog.levels.values()]


@router.get("/levels/{level_id}/datasets")
async def list_datasets(level_id: str, runs: Runs) -> list[dict[str, Any]]:
    return await runs.list_datasets(level_id)


@router.get("/levels/{level_id}/{difficulty}")
async def get_level(level_id: str, difficulty: str, catalog: Catalog) -> dict[str, Any]:
    variant = catalog.variant(level_id, difficulty)
    return level_view(variant, catalog.graphs[(level_id, difficulty)])


# --- datasets and runs ----------------------------------------------------------------------------------------


class TeamSourceBody(BaseModel):
    source: Literal["generate", "dataset"] = "generate"
    dataset_id: str | None = None


class CreateRunBody(BaseModel):
    level: str
    difficulty: str
    red: TeamSourceBody = TeamSourceBody()
    green: TeamSourceBody = TeamSourceBody()


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.get_dataset(dataset_id)


@router.post("/runs", status_code=201)
async def create_run(body: CreateRunBody, runs: Runs, user: User) -> dict[str, Any]:
    return await runs.start_run(
        body.level,
        body.difficulty,
        TeamSource(**body.red.model_dump()),
        TeamSource(**body.green.model_dump()),
        user_id=user,
    )


@router.get("/runs")
async def list_runs(runs: Runs, level: str | None = None) -> list[dict[str, Any]]:
    return await runs.list_runs(level)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.run_detail(run_id)


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.stop_run(run_id)


@router.get("/runs/{run_id}/results")
async def get_results(run_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.results(run_id)


@router.get("/runs/{run_id}/cases/{case_id}")
async def get_case(run_id: str, case_id: str, runs: Runs) -> dict[str, Any]:
    return await runs.case_detail(run_id, case_id)


@router.get("/runs/{run_id}/export")
async def export_run(run_id: str, runs: Runs) -> JSONResponse:
    return JSONResponse(
        await runs.export(run_id),
        headers={
            "Content-Disposition": f'attachment; filename="arena-run-{run_id}.json"'
        },
    )


# --- internal: multi-turn callback of the red-team driver ---------------------------------------------------


class TurnBody(BaseModel):
    message: str


@internal_router.post("/cases/{case_id}/turn")
async def internal_turn(
    case_id: str,
    body: TurnBody,
    runs: Runs,
    token: Annotated[str, Header(alias="x-arena-token")] = "",
) -> dict[str, Any]:
    return await runs.internal_turn(case_id, token, body.message)
