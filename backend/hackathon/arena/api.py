"""Arena HTTP API (`/api/v1/arena`): levels, level editing, datasets, runs, results, replay and export
(IMPLEMENTATION.md §5).

`router` is session-protected like the rest of `/api/v1`. `internal_router` serves the multi-turn callback of the
deepteam service; it is authenticated by the per-run `x-arena-token` instead and must not be routed by the gateway.
"""

from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from hackathon.api.auth import require_user
from hackathon.arena.catalog import LevelCatalog, OverrideState, UnknownLevel
from hackathon.arena.config.loader import load_levels
from hackathon.arena.config.overrides import (
    SourceErrors,
    is_editable,
    referenced_files,
)
from hackathon.arena.config.resolved import ResolvedLevel
from hackathon.arena.graph import Graph
from hackathon.arena.registry.attacks import ATTACK_TYPES
from hackathon.arena.runs.green_team import GreenGenerator
from hackathon.arena.runs.orchestrator import Orchestrator, TeamSource
from hackathon.arena.runs.red_team import DeepTeamClient
from hackathon.arena.runs.store import VersionConflict, create_store

__all__ = ["LevelCatalog", "get_level_catalog", "get_orchestrator", "router"]

# Routes with a fixed second segment under /levels/{level_id}/ (e.g. `datasets`) must be declared before
# `get_level`, which otherwise matches them as a difficulty.
router = APIRouter(prefix="/api/v1/arena", dependencies=[Depends(require_user)])

PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")


def direct_only(request: Request) -> None:
    """The internal callback is for the deepteam service, which calls the backend Service directly.

    A request that passed a proxy (frontend Caddy, the public load balancer, agentgateway) carries forwarding
    headers and is answered as if the route did not exist. The edge also denies the path; this is the second
    line, so a proxy misconfiguration cannot expose the callback.
    """
    if any(header in request.headers for header in PROXY_HEADERS):
        raise HTTPException(404, "Not Found")


internal_router = APIRouter(
    prefix="/api/v1/arena/internal", dependencies=[Depends(direct_only)]
)


@lru_cache
def get_level_catalog() -> LevelCatalog:
    """Load `backend/levels/` once per process; an invalid config raises `LevelConfigError`.

    The app attaches the run store at startup (`hackathon.main`), which serves saved overrides and enables
    editing; without it the catalog serves the repository files only.
    """
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


def level_view(
    level: ResolvedLevel, graph: Graph, state: OverrideState | None = None
) -> dict[str, Any]:
    state = state or OverrideState()
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
                {
                    "path": path,
                    "content": content,
                    "modified": path in state.active,
                    "editable": is_editable(level.id, path),
                }
                for path, content in level.sources.items()
            ]
        },
        "red_team": red_team_summary(level),
        "green_team": green_team_summary(level),
        "version": state.version,
        "modified": bool(state.active),
        "override_error": state.error_view(),
    }


def level_detail(
    catalog: LevelCatalog, level_id: str, difficulty: str | None
) -> dict[str, Any]:
    """Level view of `difficulty`, or of the first difficulty if the level has no such difficulty (any more)."""
    level = catalog.levels[level_id]
    if difficulty not in level.variants:
        difficulty = next(iter(level.variants))
    return level_view(
        level.variants[difficulty],
        catalog.graphs[(level_id, difficulty)],
        catalog.state(level_id),
    )


@router.get("/levels")
async def list_levels(catalog: Catalog) -> list[dict[str, Any]]:
    await catalog.refresh_or_cached()
    summaries = []
    for level in catalog.levels.values():
        state = catalog.state(level.id)
        summaries.append(
            {
                **level.summary(),
                "version": state.version,
                "modified": bool(state.active),
                "override_error": str(state.error) if state.error else None,
            }
        )
    return summaries


@router.get("/levels/{level_id}/datasets")
async def list_datasets(level_id: str, runs: Runs) -> list[dict[str, Any]]:
    return await runs.list_datasets(level_id)


# --- level editing (overrides on top of the repository files) ---------------------------------------------


class SourceFileBody(BaseModel):
    path: (
        str  # relative to backend/levels/, e.g. `email-review/prompts/output_judge.md`
    )
    content: str


class SaveSourceBody(BaseModel):
    files: list[SourceFileBody]
    base_version: int
    difficulty: str | None = None  # the level view returned on success


def _known(catalog: LevelCatalog, level_id: str) -> None:
    if level_id not in catalog.levels:
        raise HTTPException(404, f"Unknown level '{level_id}'")


def _editing(catalog: LevelCatalog) -> None:
    if catalog.store is None:
        raise HTTPException(503, "Level editing is not available: no arena store.")


def _conflict(
    catalog: LevelCatalog, level_id: str, difficulty: str | None, version: int
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": "Someone else saved this level in the meantime.",
            "version": version,
            "current": level_detail(catalog, level_id, difficulty),
        },
    )


@router.get("/levels/{level_id}/source")
async def get_level_source(level_id: str, catalog: Catalog) -> dict[str, Any]:
    """Every file of a level as served (all difficulties, overrides applied), e.g. to copy edits into git."""
    await catalog.refresh_or_cached()
    _known(catalog, level_id)
    state = catalog.state(level_id)
    return {
        "level": level_id,
        "version": state.version,
        "modified": bool(state.active),
        "updated_at": state.updated_at,
        "updated_by": state.updated_by,
        "override_error": state.error_view(),
        "files": [
            {
                "path": path,
                "content": content,
                "modified": path in state.active,
                "editable": is_editable(level_id, path),
                **(state.meta.get(path, {}) if path in state.active else {}),
            }
            for path, content in referenced_files(catalog.levels[level_id]).items()
        ],
    }


@router.put("/levels/{level_id}/source", response_model=None)
async def save_level_source(
    level_id: str, body: SaveSourceBody, catalog: Catalog, user: User
) -> dict[str, Any] | JSONResponse:
    """Validate the edited files with the whole level (every difficulty) and store them as overrides."""
    _editing(catalog)
    _known(catalog, level_id)
    try:
        await catalog.save(
            level_id,
            [(file.path, file.content) for file in body.files],
            base_version=body.base_version,
            user_id=user,
        )
    except UnknownLevel:
        raise HTTPException(404, f"Unknown level '{level_id}'") from None
    except VersionConflict as exc:
        return _conflict(catalog, level_id, body.difficulty, exc.version)
    except SourceErrors as exc:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "The level config is invalid; nothing was saved.",
                "errors": exc.errors,
            },
        )
    return level_detail(catalog, level_id, body.difficulty)


@router.delete("/levels/{level_id}/source", response_model=None)
async def reset_level_source(
    level_id: str,
    catalog: Catalog,
    user: User,
    difficulty: str | None = None,
    base_version: int | None = None,
) -> dict[str, Any] | JSONResponse:
    """Drop the level's overrides: it is served from the repository files again."""
    _editing(catalog)
    _known(catalog, level_id)
    try:
        await catalog.reset(level_id, base_version=base_version, user_id=user)
    except VersionConflict as exc:
        return _conflict(catalog, level_id, difficulty, exc.version)
    return level_detail(catalog, level_id, difficulty)


@router.get("/levels/{level_id}/{difficulty}")
async def get_level(level_id: str, difficulty: str, catalog: Catalog) -> dict[str, Any]:
    await catalog.refresh_or_cached()
    variant = catalog.variant(level_id, difficulty)
    return level_view(
        variant, catalog.graphs[(level_id, difficulty)], catalog.state(level_id)
    )


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
