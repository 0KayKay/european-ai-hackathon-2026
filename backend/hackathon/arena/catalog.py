"""The levels the arena serves: the repository files of `backend/levels/` plus the overrides edited in the UI.

`LevelCatalog` keeps every level resolved (all difficulties) with its graphs. Without a store it serves the
repository files only (tests, tools). With a store (attached at app startup) it applies each level's saved
overrides, and `refresh()` reloads a level whose overrides version changed. The check is a single small query
(`SELECT level, version FROM arena_level_versions`), run before level requests and run starts, so every backend
replica serves a save on its next request.

An override that no longer validates (e.g. after a schema change) never stops the app: the level falls back to
its repository version and the problem is reported as `override_error`.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from fastapi import HTTPException

from hackathon.arena.config.loader import (
    LEVELS_ROOT,
    LevelConfigError,
    LevelSet,
    load_level,
)
from hackathon.arena.config.overrides import (
    MAX_FILE_CHARACTERS,
    MAX_FILES,
    SourceErrors,
    check_path,
    config_problem,
    is_editable,
    problem,
    referenced_files,
    repository_content,
)
from hackathon.arena.config.resolved import ResolvedLevel
from hackathon.arena.graph import Graph, build_graph
from hackathon.arena.runs.store import RunStore, VersionConflict, storable

logger = structlog.get_logger(__name__)


class UnknownLevel(LookupError):
    pass


@dataclass
class OverrideState:
    """A level's stored overrides and whether they are in effect."""

    version: int = 0
    files: dict[str, str] = field(default_factory=dict)  # path -> content
    meta: dict[str, dict[str, Any]] = field(default_factory=dict)  # path -> updated_*
    updated_at: str | None = None
    updated_by: str | None = None
    # Set when the stored overrides do not load; the repository version is served instead.
    error: LevelConfigError | None = None

    @property
    def active(self) -> dict[str, str]:
        """Overrides in effect (none while they are broken)."""
        return {} if self.error else self.files

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "OverrideState":
        files = record.get("files") or {}
        return cls(
            version=record["version"],
            files={path: item["content"] for path, item in files.items()},
            meta={
                path: {key: value for key, value in item.items() if key != "content"}
                for path, item in files.items()
            },
            updated_at=record.get("updated_at"),
            updated_by=record.get("updated_by"),
        )

    def error_view(self) -> dict[str, Any] | None:
        if self.error is None:
            return None
        return {
            "message": str(self.error),
            "errors": [config_problem(self.error)],
            "files": [
                {"path": path, "content": content}
                for path, content in self.files.items()
            ],
        }


@dataclass
class LevelCatalog:
    """Levels with their graphs; with a `store`, saved overrides on top of the repository files."""

    levels: dict[str, LevelSet]
    graphs: dict[tuple[str, str], Graph]
    repository: dict[str, LevelSet] = field(default_factory=dict)
    overrides: dict[str, OverrideState] = field(default_factory=dict)
    store: RunStore | None = None
    root: Path = LEVELS_ROOT
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @classmethod
    def build(
        cls,
        levels: dict[str, LevelSet],
        *,
        store: RunStore | None = None,
        root: Path = LEVELS_ROOT,
    ) -> "LevelCatalog":
        catalog = cls(
            levels=dict(levels),
            graphs={},
            repository=dict(levels),
            store=store,
            root=root,
        )
        for level in levels.values():
            catalog._set_graphs(level)
        return catalog

    # --- lookups -------------------------------------------------------------------------------------------

    def level(self, level_id: str) -> LevelSet:
        level = self.levels.get(level_id)
        if level is None:
            raise UnknownLevel(level_id)
        return level

    def state(self, level_id: str) -> OverrideState:
        return self.overrides.get(level_id) or OverrideState()

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

    # --- overrides -----------------------------------------------------------------------------------------

    def _set_graphs(self, level: LevelSet) -> None:
        for key in [key for key in self.graphs if key[0] == level.id]:
            del self.graphs[key]
        for difficulty, variant in level.variants.items():
            self.graphs[(level.id, difficulty)] = build_graph(variant)

    def _sort(self) -> None:
        ordered = sorted(
            self.levels.values(), key=lambda level: (level.first.order, level.id)
        )
        self.levels = {level.id: level for level in ordered}

    def _apply(self, record: dict[str, Any]) -> None:
        """Serve a level with the overrides `record` (or its repository version if they do not load)."""
        level_id = record["level"]
        state = OverrideState.from_record(record)
        level = self.repository[level_id]
        if state.files:
            try:
                level = load_level(self.root / level_id, self.root, state.files)
            except LevelConfigError as exc:
                logger.warning(
                    "Saved level overrides do not load; serving the repository version",
                    level=level_id,
                    version=state.version,
                    error=str(exc),
                )
                state.error = exc
        self.overrides[level_id] = state
        self.levels[level_id] = level
        self._set_graphs(level)
        self._sort()

    async def refresh(self) -> None:
        """Reload every level whose overrides version changed in the store (cheap when nothing changed)."""
        if self.store is None:
            return
        async with self._lock:
            versions = await self.store.level_override_versions()
            for level_id in self.repository:
                if versions.get(level_id, 0) != self.state(level_id).version:
                    self._apply(await self.store.get_level_overrides(level_id))

    async def refresh_or_cached(self) -> None:
        """`refresh()` for read requests: if the store is unreachable, serve what is loaded."""
        try:
            await self.refresh()
        except Exception:
            logger.exception(
                "Could not check the level overrides; serving cached levels"
            )

    def _store(self) -> RunStore:
        if self.store is None:
            raise RuntimeError("level editing needs the arena store")
        return self.store

    async def save(
        self,
        level_id: str,
        files: list[tuple[str, str]],
        *,
        base_version: int,
        user_id: str,
    ) -> LevelSet:
        """Validate and store edited files of a level; returns the level as now served.

        Raises `UnknownLevel`, `VersionConflict` (stale `base_version`) or `SourceErrors` (422 items).
        """
        store = self._store()
        self.level(level_id)
        await self.refresh()
        state = self.state(level_id)
        if base_version != state.version:
            raise VersionConflict(state.version)

        errors, submitted = [], {}
        if len(files) > MAX_FILES:
            errors.append(problem("", f"at most {MAX_FILES} files per save"))
        for path, content in files:
            if (reason := check_path(level_id, path, self.root)) is not None:
                errors.append(problem(path, reason))
            elif path in submitted:
                errors.append(problem(path, "the file is listed twice"))
            elif len(content) > MAX_FILE_CHARACTERS:
                errors.append(
                    problem(path, f"larger than {MAX_FILE_CHARACTERS} characters")
                )
            else:
                # Stored exactly as validated: text that Postgres cannot keep is replaced first.
                submitted[path] = storable(content)
        if errors:
            raise SourceErrors(errors)

        # Edits apply on top of the overrides in effect; broken overrides are replaced by this save.
        merged = {**state.active, **submitted}
        try:
            level = load_level(self.root / level_id, self.root, merged)
        except LevelConfigError as exc:
            raise SourceErrors([config_problem(exc)]) from None
        referenced = referenced_files(level)
        unused = [path for path in submitted if path not in referenced]
        if unused:
            raise SourceErrors(
                [
                    problem(
                        path,
                        "the level does not use this file: reference it from level.yaml in the same save",
                    )
                    for path in unused
                ]
            )
        # Keep only real changes: an override that equals the repository file, or that the level no longer
        # references, is dropped.
        overrides = {
            path: content
            for path, content in merged.items()
            if path in referenced
            and is_editable(level_id, path)
            and content != repository_content(self.root, path)
        }
        async with self._lock:
            record = await store.save_level_overrides(
                level_id, overrides, base_version=base_version, user_id=user_id
            )
            self._apply(record)
        logger.info(
            "Level overrides saved",
            level=level_id,
            version=record["version"],
            files=sorted(overrides),
            user_id=user_id,
        )
        return self.levels[level_id]

    async def reset(
        self, level_id: str, *, base_version: int | None, user_id: str
    ) -> LevelSet:
        """Drop a level's overrides: it is served from the repository files again."""
        store = self._store()
        self.level(level_id)
        await self.refresh()
        state = self.state(level_id)
        if base_version is not None and base_version != state.version:
            raise VersionConflict(state.version)
        async with self._lock:
            record = await store.save_level_overrides(
                level_id, {}, base_version=state.version, user_id=user_id
            )
            self._apply(record)
        logger.info("Level overrides reset", level=level_id, user_id=user_id)
        return self.levels[level_id]
