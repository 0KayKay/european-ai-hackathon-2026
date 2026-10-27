"""Arena HTTP API (`/api/v1/arena`): level list and level view data (IMPLEMENTATION.md §5)."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from hackathon.api.auth import require_user
from hackathon.arena.config.loader import LevelSet, load_levels
from hackathon.arena.config.resolved import ResolvedLevel
from hackathon.arena.graph import Graph, build_graph
from hackathon.arena.registry.attacks import ATTACK_TYPES

# Routes with a fixed second segment under /levels/{level_id}/ (e.g. `datasets`) must be declared before
# `get_level`, which otherwise matches them as a difficulty.
router = APIRouter(prefix="/api/v1/arena", dependencies=[Depends(require_user)])


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


@router.get("/levels/{level_id}/{difficulty}")
async def get_level(level_id: str, difficulty: str, catalog: Catalog) -> dict[str, Any]:
    variant = catalog.variant(level_id, difficulty)
    return level_view(variant, catalog.graphs[(level_id, difficulty)])
