"""Evaluation runs: generate red and green cases, execute them serially, record everything (SPEC §4.4–§4.7).

A run is created by `start_run()` and executed in a background asyncio task:

- `generating`: red cases (DeepTeam, or a stored dataset) and green cases (generator, or a stored dataset) are
  created; every case is stored as soon as it exists, so the inputs can be inspected early (R-RED-4).
  Single-turn generated cases are stored as datasets (red only for indirect levels, R-DAT-1).
- `running`: every case runs to completion, one after the other (R-RUN-2, R-RUN-3). Single-turn cases use
  `run_case()`; multi-turn red cases are driven by the red-team source through the internal turn callback.
- `completed` / `stopped` (stop request) / `failed` (unexpected error) / `interrupted` (process shutdown or
  restart). Completed cases are kept in every case.

The per-run callback token lives in memory only; records never contain headers or credentials (R-EXE-8).
"""

import asyncio
import hmac
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import uuid4

import structlog
from fastapi import HTTPException

from hackathon.arena.config.resolved import ResolvedLevel
from hackathon.arena.engine import (
    Case,
    CaseEvent,
    CaseInput,
    CaseResult,
    CaseSession,
    CaseUsage,
    EngineModels,
    case_model_roles,
    run_case,
)
from hackathon.arena.registry.attacks import ATTACK_TYPES
from hackathon.arena.runs import results as result_views
from hackathon.arena.runs.green_team import GreenTeamError
from hackathon.arena.runs.red_team import (
    RedTeamError,
    RedTeamSource,
    TurnCallback,
    check_generated,
)
from hackathon.arena.runs.store import (
    ACTIVE_STATUSES,
    RunStore,
    case_order,
    now_iso,
)
from hackathon.config import get_settings

if TYPE_CHECKING:
    from hackathon.arena.api import LevelCatalog

logger = structlog.get_logger(__name__)

Team = Literal["red", "green"]
STOP_TIMEOUT_SECONDS = 10


class GreenSource(Protocol):
    def generate(
        self, level: ResolvedLevel, *, count: int, user_id: str, run_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Benign cases `{input}` (direct) or `{content}` (indirect); raises `GreenTeamError`."""
        ...


@dataclass
class TeamSource:
    source: Literal["generate", "dataset"] = "generate"
    dataset_id: str | None = None


@dataclass
class ActiveCase:
    """A multi-turn case that currently accepts turns through the internal callback."""

    token: str
    session: CaseSession
    record: dict[str, Any]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class ActiveRun:
    run: dict[str, Any]
    level: ResolvedLevel
    token: str
    datasets: dict[str, dict[str, Any] | None]
    cases: list[dict[str, Any]] = field(default_factory=list)
    live: dict[str, Any] | None = None
    # The executing case, until its result is saved.
    current: dict[str, Any] | None = None
    task: asyncio.Task | None = None
    cancel_status: str = "stopped"

    def cancel(self, status: str) -> None:
        """Cancel the run task once; a second cancel would interrupt it while it saves its final state."""
        if self.task and not self.task.done() and not self.task.cancelling():
            self.cancel_status = status
            self.task.cancel()


def _seconds_between(start: str, end: str | None) -> float:
    begin = datetime.fromisoformat(start)
    finish = datetime.fromisoformat(end) if end else datetime.now(UTC)
    return round(max((finish - begin).total_seconds(), 0.0), 3)


def _dataset_case(case: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": case["id"],
        "team": case["team"],
        "attack_type": case.get("attack_type"),
    }
    for key in ("input", "content"):
        if case.get(key) is not None:
            item[key] = case[key]
    return item


class Orchestrator:
    def __init__(
        self,
        catalog: "LevelCatalog",
        store: RunStore,
        red_team: RedTeamSource,
        green_team: GreenSource,
        *,
        models: EngineModels | None = None,
        callback_base_url: str | None = None,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.red_team = red_team
        self.green_team = green_team
        self.models = models or EngineModels()
        self.callback_base_url = (
            callback_base_url or get_settings().ARENA_CALLBACK_URL
        ).rstrip("/")
        self.active: dict[str, ActiveRun] = {}
        self.turn_targets: dict[str, ActiveCase] = {}

    # --- lifecycle -------------------------------------------------------------------------------------------

    async def startup(self) -> int:
        """Runs left `generating`/`running` by an earlier process become `interrupted`."""
        count = await self.store.setup()
        if count:
            logger.info("Marked arena runs as interrupted", count=count)
        return count

    async def shutdown(self) -> None:
        for active in list(self.active.values()):
            active.cancel("interrupted")
        tasks = [active.task for active in self.active.values() if active.task]
        if tasks:
            await asyncio.wait(tasks, timeout=STOP_TIMEOUT_SECONDS)
        await self.store.close()

    async def wait(self, run_id: str) -> None:
        """Wait until a run's background task has finished (tests, shutdown)."""
        active = self.active.get(run_id)
        if active and active.task:
            await asyncio.wait({active.task})

    # --- start / stop ----------------------------------------------------------------------------------------

    def _variant(self, level_id: str, difficulty: str) -> ResolvedLevel:
        level = self.catalog.levels.get(level_id)
        if level is None:
            raise HTTPException(404, f"Unknown level '{level_id}'")
        variant = level.variants.get(difficulty)
        if variant is None:
            raise HTTPException(
                404, f"Level '{level_id}' has no difficulty '{difficulty}'"
            )
        return variant

    async def _source_dataset(
        self, level: ResolvedLevel, team: Team, source: TeamSource
    ) -> dict[str, Any] | None:
        if source.source == "generate":
            return None
        if team == "red" and level.surface.channel == "direct":
            raise HTTPException(
                400,
                "Red cases of a direct level are generated during each run; "
                "only red datasets of indirect levels can be reused.",
            )
        if not source.dataset_id:
            raise HTTPException(
                400, f"{team}.dataset_id is required for source 'dataset'"
            )
        dataset = await self.store.get_dataset(source.dataset_id)
        if dataset is None:
            raise HTTPException(404, f"Unknown dataset '{source.dataset_id}'")
        if dataset["level"] != level.id:
            raise HTTPException(
                400, f"Dataset '{dataset['id']}' belongs to level '{dataset['level']}'"
            )
        if dataset["team"] != team:
            raise HTTPException(
                400, f"Dataset '{dataset['id']}' is a {dataset['team']} dataset"
            )
        return dataset

    def _models(
        self, level: ResolvedLevel, datasets: dict[str, dict[str, Any] | None]
    ) -> list[dict[str, Any]]:
        """LLM roles used by this run (R-EXE-7): case roles plus the generators, from the dataset if reused."""
        roles = level.model_roles()
        models = case_model_roles(level)
        for team, prefix in (("red", "red_team."), ("green", "green_team.")):
            dataset = datasets[team]
            if dataset is not None:
                models += dataset.get("models", [])
            else:
                models += [role for role in roles if role["role"].startswith(prefix)]
        return models

    async def start_run(
        self,
        level_id: str,
        difficulty: str,
        red: TeamSource,
        green: TeamSource,
        *,
        user_id: str = "arena",
    ) -> dict[str, Any]:
        # Saved level edits (possibly from another replica) apply to every new run; a run keeps the snapshot
        # of the level it started with.
        await self.catalog.refresh()
        level = self._variant(level_id, difficulty)
        # Taken together with the level: a save during the awaits below must not mix two versions.
        graph = self.catalog.graphs[(level.id, level.difficulty)]
        datasets = {
            "red": await self._source_dataset(level, "red", red),
            "green": await self._source_dataset(level, "green", green),
        }
        plan = {
            "red": len(datasets["red"]["cases"])
            if datasets["red"]
            else sum(attack.attempts for attack in level.red_team.attacks),
            "green": len(datasets["green"]["cases"])
            if datasets["green"]
            else level.green_team.cases,
        }
        run = {
            "id": str(uuid4()),
            "level": level.id,
            "difficulty": level.difficulty,
            "title": level.title,
            "status": "generating",
            "created_by": user_id,
            "started_at": now_iso(),
            "finished_at": None,
            "error": None,
            "sources": {
                "red": {"source": red.source, "dataset_id": red.dataset_id},
                "green": {"source": green.source, "dataset_id": green.dataset_id},
            },
            "plan": plan,
            "datasets": {
                team: dataset["id"] if dataset else None
                for team, dataset in datasets.items()
            },
            "generation": {
                team: {
                    "status": "dataset" if datasets[team] else "pending",
                    "error": None,
                }
                for team in ("red", "green")
            },
            "models": self._models(level, datasets),
            "counts": result_views.counts([]),
            "progress": {
                team: {"done": 0, "total": plan[team]} for team in ("red", "green")
            },
            "snapshot": {
                "level": level.model_dump(mode="json"),
                "graph": graph.model_dump(mode="json"),
            },
        }
        await self.store.save_run(run)
        active = ActiveRun(
            run=run, level=level, token=secrets.token_urlsafe(32), datasets=datasets
        )
        self.active[run["id"]] = active
        active.task = asyncio.create_task(
            self._execute(active), name=f"arena-run-{run['id']}"
        )
        logger.info(
            "Arena run started", run_id=run["id"], level=level.id, difficulty=difficulty
        )
        return self.summary(run)

    async def stop_run(self, run_id: str) -> dict[str, Any]:
        active = self.active.get(run_id)
        if active and active.task and not active.task.done():
            active.cancel("stopped")
            await asyncio.wait({active.task}, timeout=STOP_TIMEOUT_SECONDS)
            return self.summary(active.run)
        run = await self._stored_run(run_id)
        if run["status"] in ACTIVE_STATUSES:  # owned by no task in this process
            run = {
                **run,
                "status": "stopped",
                "finished_at": run["finished_at"] or now_iso(),
            }
            await self.store.save_run(run)
        return self.summary(run)

    # --- execution -------------------------------------------------------------------------------------------

    async def _save(self, active: ActiveRun, *cases: dict[str, Any]) -> None:
        run = active.run
        run["counts"] = result_views.counts(active.cases)
        run["progress"] = result_views.progress(run, active.cases)
        for case in cases:
            await self.store.save_case(case)
        await self.store.save_run(run)

    async def _execute(self, active: ActiveRun) -> None:
        run = active.run
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._prepare_red(active))
                group.create_task(self._prepare_green(active))
            run["status"] = "running"
            await self._save(active)
            for case in sorted(active.cases, key=case_order):
                if case["status"] == "pending":
                    await self._execute_case(active, case)
            run["status"] = "completed"
        except asyncio.CancelledError:
            run["status"] = active.cancel_status
        except Exception as exc:
            logger.exception("Arena run failed", run_id=run["id"])
            while isinstance(exc, ExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            run["status"] = "failed"
            run["error"] = f"The run failed ({type(exc).__name__})."
        finally:
            run["finished_at"] = now_iso()
            try:
                await self._settle_current(active)
            except Exception:
                logger.exception("Could not save the last arena case", run_id=run["id"])
            active.live = None
            try:
                await self._save(active)
            except Exception:
                logger.exception(
                    "Could not save the final arena run state", run_id=run["id"]
                )
            self.active.pop(run["id"], None)
            logger.info("Arena run finished", run_id=run["id"], status=run["status"])

    async def _settle_current(self, active: ActiveRun) -> None:
        """Save the case that was executing when the run ended (stop, shutdown, failure).

        An undecided case becomes `stopped` with its events so far. A decided one is saved again, because the
        run may have ended while its result was being saved.
        """
        case, active.current = active.current, None
        if case is None:
            return
        if case["status"] == "running":
            case["status"] = "stopped"
            if active.live and active.live["case_id"] == case["id"]:
                case["events"] = active.live["events"]
        await self.store.save_case(case)

    def _new_case(
        self,
        active: ActiveRun,
        team: Team,
        *,
        attack_type: str | None = None,
        **values: Any,
    ) -> dict[str, Any]:
        index = 1 + sum(1 for case in active.cases if case["team"] == team)
        turns_mode = (
            ATTACK_TYPES[attack_type].turns if attack_type in ATTACK_TYPES else "single"
        )
        case = {
            "id": str(uuid4()),
            "run_id": active.run["id"],
            "team": team,
            "index": index,
            "attack_type": attack_type,
            "mode": turns_mode if team == "red" else "single",
            "input": None,
            "content": None,
            "messages": None,
            "dataset_case_id": None,
            "status": "pending",
            "outcome": None,
            "decided_by": None,
            "turns": 0,
            "success": None,
            "usage": None,
            "models": [],
            "error": None,
            "created_at": now_iso(),
            "started_at": None,
            "finished_at": None,
            "duration": None,
            "events": [],
            **values,
        }
        active.cases.append(case)
        return case

    def _failed_case(
        self, active: ActiveRun, team: Team, message: str, **values: Any
    ) -> dict[str, Any]:
        """A case that could not be generated or was generated invalid: decided as an error, never executed."""
        return self._new_case(
            active,
            team,
            status="done",
            outcome="error",
            decided_by={"kind": "error"},
            error=message,
            **values,
        )

    async def _from_dataset(self, active: ActiveRun, team: Team) -> None:
        dataset = active.datasets[team]
        for item in dataset["cases"]:
            case = self._new_case(
                active,
                team,
                attack_type=item.get("attack_type"),
                input=item.get("input"),
                content=item.get("content"),
                dataset_case_id=item["id"],
            )
            await self._save(active, case)

    async def _store_dataset(
        self, active: ActiveRun, team: Team, cases: list[dict[str, Any]]
    ) -> None:
        if not cases:
            return
        run, level = active.run, active.level
        created = now_iso()
        stamp = created[:16].replace("T", " ")
        dataset = {
            "id": str(uuid4()),
            "name": f"{team.capitalize()} cases · {level.difficulty} run · {stamp} UTC",
            "level": level.id,
            "team": team,
            "created_at": created,
            "size": len(cases),
            "run_id": run["id"],
            "difficulty": level.difficulty,
            "models": [
                model
                for model in run["models"]
                if model["role"].startswith(
                    "red_team." if team == "red" else "green_team."
                )
            ],
            "cases": [_dataset_case(case) for case in cases],
        }
        await self.store.save_dataset(dataset)
        run["datasets"][team] = dataset["id"]

    async def _prepare_red(self, active: ActiveRun) -> None:
        if active.datasets["red"] is not None:
            await self._from_dataset(active, "red")
            return
        run, level = active.run, active.level
        generation = run["generation"]["red"]
        generation["status"] = "generating"
        single = [
            (attack.type, attack.attempts)
            for attack in level.red_team.attacks
            if ATTACK_TYPES[attack.type].turns == "single"
        ]
        multi = [
            (attack.type, attack.attempts)
            for attack in level.red_team.attacks
            if ATTACK_TYPES[attack.type].turns == "multi"
        ]
        valid: list[dict[str, Any]] = []
        if single:
            remaining = dict(single)
            try:
                async for raw in self.red_team.generate(
                    level, run_id=run["id"], attacks=single
                ):
                    values, problem = check_generated(level, raw)
                    attack_type = values.pop("attack_type", None)
                    if remaining.get(attack_type, 0) <= 0:
                        logger.warning(
                            "Ignored a generated case of an unrequested type or beyond its count",
                            run_id=run["id"],
                            attack_type=attack_type,
                        )
                        continue
                    remaining[attack_type] -= 1
                    if problem:
                        case = self._failed_case(
                            active,
                            "red",
                            f"Invalid generated case: {problem}",
                            attack_type=attack_type,
                            **values,
                        )
                    else:
                        case = self._new_case(
                            active, "red", attack_type=attack_type, **values
                        )
                        valid.append(case)
                    await self._save(active, case)
            except RedTeamError as exc:
                logger.warning(
                    "Red-team generation failed", run_id=run["id"], error=str(exc)
                )
                generation["error"] = str(exc)
            except Exception as exc:
                logger.exception("Red-team generation failed", run_id=run["id"])
                generation["error"] = f"Unexpected error ({type(exc).__name__})."
            missing_reason = (
                f"Red-team generation failed: {generation['error']}"
                if generation["error"]
                else "Red-team generation returned fewer cases than configured."
            )
            for attack_type, left in remaining.items():
                for _ in range(left):
                    case = self._failed_case(
                        active, "red", missing_reason, attack_type=attack_type
                    )
                    await self._save(active, case)
        for attack_type, attempts in multi:
            for _ in range(attempts):
                await self._save(
                    active, self._new_case(active, "red", attack_type=attack_type)
                )
        if level.surface.channel == "indirect":
            await self._store_dataset(active, "red", valid)
        generation["status"] = "failed" if generation["error"] else "done"
        await self._save(active)

    async def _prepare_green(self, active: ActiveRun) -> None:
        if active.datasets["green"] is not None:
            await self._from_dataset(active, "green")
            return
        run, level = active.run, active.level
        generation = run["generation"]["green"]
        generation["status"] = "generating"
        count = run["plan"]["green"]
        valid: list[dict[str, Any]] = []
        try:
            async for values in self.green_team.generate(
                level, count=count, user_id=run["created_by"], run_id=run["id"]
            ):
                if len(valid) >= count:
                    break
                case = self._new_case(active, "green", **values)
                valid.append(case)
                await self._save(active, case)
        except GreenTeamError as exc:
            logger.warning(
                "Green-team generation failed", run_id=run["id"], error=str(exc)
            )
            generation["error"] = str(exc)
        except Exception as exc:
            logger.exception("Green-team generation failed", run_id=run["id"])
            generation["error"] = (
                f"Green-team generation failed ({type(exc).__name__})."
            )
        for _ in range(count - len(valid)):
            reason = (
                generation["error"] or "Green-team generation returned fewer cases."
            )
            await self._save(active, self._failed_case(active, "green", f"{reason}"))
        await self._store_dataset(active, "green", valid)
        generation["status"] = "failed" if generation["error"] else "done"
        await self._save(active)

    async def _execute_case(self, active: ActiveRun, case: dict[str, Any]) -> None:
        level = active.level
        active.current = case
        case["status"] = "running"
        case["started_at"] = now_iso()
        live = {"case_id": case["id"], "events": []}
        active.live = live

        async def listener(event: CaseEvent) -> None:
            live["events"].append(event.model_dump(mode="json"))

        options = {
            "user_id": active.run["created_by"],
            "models": self.models,
            "listener": listener,
        }
        await self._save(active, case)
        start = time.monotonic()
        identity = Case(
            id=case["id"], team=case["team"], attack_type=case["attack_type"]
        )
        try:
            if case["mode"] == "multi":
                result = await self._drive_multi_turn(active, case, identity, options)
            else:
                result = await run_case(
                    level,
                    CaseInput(
                        identity,
                        messages=[case["input"]] if case["input"] is not None else None,
                        content=case["content"],
                    ),
                    **options,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Arena case failed", case_id=case["id"])
            result = CaseResult(
                case_id=case["id"],
                team=case["team"],
                attack_type=case["attack_type"],
                outcome="error",
                decided_by={"kind": "error"},
                turns=0,
                success={"met": False, "kind": level.success.kind, "detail": {}},
                events=[CaseEvent.model_validate(event) for event in live["events"]],
                usage=CaseUsage(),
                models=case_model_roles(level),
                error=f"The case could not run ({type(exc).__name__}).",
            )
        self._apply_result(case, result, time.monotonic() - start)
        active.live = None
        await self._save(active, case)
        active.current = None

    @staticmethod
    def _apply_result(
        case: dict[str, Any], result: CaseResult, duration: float
    ) -> None:
        case.update(
            status="done",
            outcome=result.outcome,
            decided_by=result.decided_by,
            turns=result.turns,
            success=result.success,
            usage=result.usage.model_dump(),
            models=result.models,
            error=result.error,
            events=[event.model_dump(mode="json") for event in result.events],
            finished_at=now_iso(),
            duration=round(duration, 3),
        )

    async def _drive_multi_turn(
        self,
        active: ActiveRun,
        case: dict[str, Any],
        identity: Case,
        options: dict[str, Any],
    ) -> CaseResult:
        level = active.level
        session = CaseSession(level, identity, **options)
        case["messages"] = []
        target = ActiveCase(token=active.token, session=session, record=case)
        self.turn_targets[case["id"]] = target
        callback = TurnCallback(
            url=f"{self.callback_base_url}/api/v1/arena/internal/cases/{case['id']}/turn",
            token=active.token,
            call=partial(self.internal_turn, case["id"], active.token),
        )
        error = None
        try:
            await self.red_team.drive(
                level,
                case_id=case["id"],
                attack_type=case["attack_type"],
                max_turns=level.surface.max_turns,
                callback=callback,
            )
        except RedTeamError as exc:
            error = f"Red-team driver failed: {exc}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Red-team driver failed", case_id=case["id"])
            error = f"Red-team driver failed ({type(exc).__name__})."
        finally:
            self.turn_targets.pop(case["id"], None)
        # The driver may give up while a turn it requested still runs; decide the case after that turn.
        async with target.lock:
            return await session.finish(error=error)

    async def internal_turn(
        self, case_id: str, token: str, message: str
    ) -> dict[str, Any]:
        """One attacker turn of a multi-turn case, from the red-team driver: `{reply, stop}`."""
        target = self.turn_targets.get(case_id)
        if target is None:
            raise HTTPException(404, "Unknown or finished case")
        if not hmac.compare_digest(token.encode(), target.token.encode()):
            raise HTTPException(403, "Invalid arena token")
        async with target.lock:
            if target.session.stopped or target.session.finished:
                return {"reply": "", "stop": True}
            target.record["messages"].append(message)
            result = await target.session.turn(message)
            target.record["turns"] = result.turn
        return {"reply": result.reply, "stop": result.stop}

    # --- views -----------------------------------------------------------------------------------------------

    async def _stored_run(self, run_id: str) -> dict[str, Any]:
        run = await self.store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return run

    async def _run_and_cases(
        self, run_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]], ActiveRun | None]:
        active = self.active.get(run_id)
        if active is not None:
            return active.run, active.cases, active
        run = await self._stored_run(run_id)
        return run, await self.store.list_cases(run_id), None

    @staticmethod
    def summary(run: dict[str, Any], *, title: str | None = None) -> dict[str, Any]:
        """Run summary. `title` is the level title at run time (recorded with the run, else from its
        snapshot, else the given fallback, else the level id); `rates` follow R-RES-1 from the recorded `counts`."""
        snapshot = run.get("snapshot") or {}
        return {
            "id": run["id"],
            "level": run["level"],
            "difficulty": run["difficulty"],
            "title": run.get("title")
            or snapshot.get("level", {}).get("title")
            or title
            or run["level"],
            "status": run["status"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "elapsed": _seconds_between(run["started_at"], run["finished_at"]),
            "progress": run["progress"],
            "counts": run["counts"],
            "rates": result_views.summary_rates(run["counts"]),
            "error": run.get("error"),
            "generation": run.get("generation"),
        }

    def _detail(
        self, run: dict[str, Any], cases: list[dict[str, Any]], active: ActiveRun | None
    ) -> dict[str, Any]:
        live = None
        if active is not None and active.live is not None:
            live = {
                "case_id": active.live["case_id"],
                "events": list(active.live["events"]),
            }
        return {
            **self.summary(run),
            "title": run["snapshot"]["level"]["title"],
            "graph": run["snapshot"]["graph"],
            "models": run["models"],
            "datasets": run["datasets"],
            "sources": run["sources"],
            "cases": [
                result_views.case_summary(run, case)
                for case in sorted(cases, key=case_order)
            ],
            "live": live,
        }

    async def run_detail(self, run_id: str) -> dict[str, Any]:
        return self._detail(*await self._run_and_cases(run_id))

    async def list_runs(self, level: str | None = None) -> list[dict[str, Any]]:
        runs = await self.store.list_runs(level)
        return [
            self.summary(
                self.active[run["id"]].run if run["id"] in self.active else run,
                # Runs stored before the title was recorded (the list leaves out the snapshot).
                title=self._level_title(run["level"]),
            )
            for run in runs
        ]

    def _level_title(self, level_id: str) -> str | None:
        level = self.catalog.levels.get(level_id)
        return level.first.title if level is not None else None

    async def results(self, run_id: str) -> dict[str, Any]:
        run, cases, _ = await self._run_and_cases(run_id)
        return result_views.results(run, sorted(cases, key=case_order))

    def _case_detail(
        self, run: dict[str, Any], case: dict[str, Any], *, graph: bool
    ) -> dict[str, Any]:
        detail = {
            **result_views.case_summary(run, case),
            "input": case.get("input"),
            "content": case.get("content"),
            "messages": case.get("messages"),
            "success": case.get("success"),
            "usage": case.get("usage"),
            "events": case.get("events", []),
        }
        if graph:
            detail["graph"] = run["snapshot"]["graph"]
        return detail

    async def case_detail(self, run_id: str, case_id: str) -> dict[str, Any]:
        active = self.active.get(run_id)
        if active is not None:
            case = next((c for c in active.cases if c["id"] == case_id), None)
            if case is not None:
                case = dict(case)
                if active.live and active.live["case_id"] == case_id:
                    case["events"] = list(active.live["events"])
                return self._case_detail(active.run, case, graph=True)
        run = await self._stored_run(run_id)
        case = await self.store.get_case(run_id, case_id)
        if case is None:
            raise HTTPException(404, "Case not found")
        return self._case_detail(run, case, graph=True)

    async def export(self, run_id: str) -> dict[str, Any]:
        """Everything recorded for a run (R-RES-6): run, models, snapshot, datasets, cases with events."""
        active = self.active.get(run_id)
        run = active.run if active else await self._stored_run(run_id)
        cases = await self.store.list_cases(run_id, events=True)
        if active is not None:
            cases = active.cases
        datasets = {
            team: await self.store.get_dataset(dataset_id) if dataset_id else None
            for team, dataset_id in run["datasets"].items()
        }
        detail = self._detail(run, cases, None)
        return {
            "run": detail,
            "models": run["models"],
            "datasets": datasets,
            "snapshot": run["snapshot"],
            "results": result_views.results(run, sorted(cases, key=case_order)),
            "cases": [
                self._case_detail(run, case, graph=False)
                for case in sorted(cases, key=case_order)
            ],
        }

    async def list_datasets(self, level_id: str) -> list[dict[str, Any]]:
        if level_id not in self.catalog.levels:
            raise HTTPException(404, f"Unknown level '{level_id}'")
        return await self.store.list_datasets(level_id)

    async def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        dataset = await self.store.get_dataset(dataset_id)
        if dataset is None:
            raise HTTPException(404, "Dataset not found")
        return dataset
