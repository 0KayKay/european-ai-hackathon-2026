"""Load level files from `backend/levels/*/level.yaml`.

For each difficulty the variant is deep-merged over the base (dicts merge, lists and scalars are replaced), the
result is validated against the schema, referenced prompt and data files are loaded and rendered, and cross
references are checked. Every problem raises `LevelConfigError` naming the file and the problem (R-CFG-5).

File conventions:
- paths are relative to the level directory, falling back to the levels root (`shared/prompts/refusal.md`,
  `hidden-mailbox/prompts/system.md`); they must stay inside the levels root;
- prompt files are used verbatim except that one final newline is removed;
- `{{name}}` placeholders: level `variables`, `user_task` (indirect levels) and, in the agent's system prompt,
  `security_prompt` are rendered at load time; runtime placeholders (`content` in judge policies, `user_context`
  and `args.<parameter>` in tool-authorization policies, `secret` and `user_email` in tool data) are kept for the
  engine; anything else is a load error;
- top-level keys starting with `x-` are ignored (room for YAML anchors).

`overrides` (path relative to the levels root -> content) replace or add files of one level without touching the
repository; the level editor of the UI validates and stores them (`hackathon.arena.config.overrides`).
"""

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from pydantic import BaseModel, ValidationError

from hackathon.arena.config.resolved import (
    PromptFile,
    ResolvedAgent,
    ResolvedGreenTeam,
    ResolvedGuardrail,
    ResolvedLevel,
    ResolvedToolset,
    ToolInfo,
)
from hackathon.arena.config.schema import GuardrailConfig, LevelVariant, ToolsetConfig
from hackathon.arena.config.templates import TemplateError, placeholders, render_partial
from hackathon.arena.registry.attacks import ATTACK_TYPES
from hackathon.arena.registry.guardrails import (
    AROUND,
    FLOW_POSITIONS,
    GUARDRAIL_KINDS,
    ON_BLOCK,
    position_key,
)
from hackathon.arena.registry.secrets import SECRET_KINDS
from hackathon.arena.registry.success import SUCCESS_KINDS
from hackathon.arena.registry.tools import (
    TOOL_DATA_RUNTIME,
    TOOL_KINDS,
    ToolState,
    build_toolset,
)

LEVELS_ROOT = Path(__file__).resolve().parents[3] / "levels"
LEVEL_FILE = "level.yaml"
BUILTIN_VARIABLES = frozenset({"security_prompt", "user_task"})


class LevelConfigError(ValueError):
    """An invalid level config; the message starts with the offending file.

    `path` is the file relative to the levels root (`email-review/level.yaml`) and `line` the 1-based line of
    the problem when the YAML parser or the validator can tell.
    """

    def __init__(
        self,
        file: str,
        problem: str,
        *,
        path: str | None = None,
        line: int | None = None,
    ) -> None:
        self.file = file
        self.problem = problem
        self.path = path if path is not None else (file.partition("/")[2] or file)
        self.line = line
        super().__init__(f"{file}: {problem}")


@dataclass
class LevelSet:
    """All difficulties of one level file."""

    id: str
    file: str
    variants: dict[str, ResolvedLevel] = field(default_factory=dict)

    @property
    def first(self) -> ResolvedLevel:
        return next(iter(self.variants.values()))

    def summary(self) -> dict[str, Any]:
        level = self.first
        return {
            "id": self.id,
            "title": level.title,
            "description": level.description,
            "channel": level.surface.channel,
            "turns": level.surface.turns,
            "difficulties": list(self.variants),
        }


def humanize(identifier: str) -> str:
    """Default display label of an id: `send-email-judge` -> `Send email judge`."""
    return identifier.replace("-", " ").replace("_", " ").capitalize()


def deep_merge(base: Any, override: Any) -> Any:
    """Merge `override` over `base`: dicts merge recursively, everything else (lists too) is replaced."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = (
                deep_merge(base[key], value) if key in base else deepcopy(value)
            )
        return merged
    return deepcopy(override)


def _loc(loc: tuple) -> str:
    text = ""
    for part in loc:
        text += (
            f"[{part}]"
            if isinstance(part, int)
            else (f".{part}" if text else str(part))
        )
    return text or "(root)"


def _validation_problem(error: ValidationError, prefix: str = "") -> str:
    problems = []
    for item in error.errors(include_url=False):
        message = item["msg"].removeprefix("Value error, ")
        problems.append(f"{prefix}{_loc(item['loc'])}: {message}")
    return "; ".join(problems)


# --- line numbers of problems ---------------------------------------------------------------------------------

_LOCATION_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def location_parts(where: str) -> list[str | int]:
    """`guardrails[3].at` -> `["guardrails", 3, "at"]`."""
    return [
        int(index) if index else name for name, index in _LOCATION_PART.findall(where)
    ]


def _find(node: Node | None, parts: list[str | int]) -> tuple[int, int | None]:
    """Walk `parts` into a composed YAML node: (matched depth, 1-based line of the deepest match)."""
    depth, line = 0, None
    for part in parts:
        if isinstance(node, MappingNode):
            entry = next(
                (
                    (key, value)
                    for key, value in node.value
                    if isinstance(key, ScalarNode) and key.value == str(part)
                ),
                None,
            )
            if entry is None:
                break
            line, node = entry[0].start_mark.line + 1, entry[1]
        elif isinstance(node, SequenceNode) and isinstance(part, int):
            if part >= len(node.value):
                break
            node = node.value[part]
            line = node.start_mark.line + 1
        else:
            break
        depth += 1
    return depth, line


def _compose(text: str) -> Node | None:
    try:
        return yaml.compose(text)
    except yaml.YAMLError:
        return None


def yaml_line(text: str, parts: list[str | int]) -> int | None:
    """Line of the deepest existing key of `parts` in a YAML text, if any key matches."""
    depth, line = _find(_compose(text), parts)
    return line if depth else None


def level_line(text: str, difficulty: str | None, parts: list[str | int]) -> int | None:
    """Line in a level file of a location in a resolved difficulty.

    A difficulty deep-merges over the base, so the location is looked up under `difficulties.<name>` and in the
    base; the deeper match wins, the difficulty on a tie.
    """
    root = _compose(text)
    candidates = []
    if difficulty is not None:
        depth, line = _find(root, ["difficulties", difficulty, *parts])
        if depth > 2:
            candidates.append((depth - 2, 1, line))
    depth, line = _find(root, parts)
    if depth:
        candidates.append((depth, 0, line))
    return max(candidates)[2] if candidates else None


def _yaml_error_line(exc: yaml.YAMLError) -> int | None:
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    return mark.line + 1 if mark is not None else None


def _placeholder_line(text: str, name: str | None) -> int | None:
    if name is None:
        return None
    match = re.search(r"\{\{\s*" + re.escape(name) + r"\s*\}\}", text)
    return text.count("\n", 0, match.start()) + 1 if match else None


def _display(path: Path, root: Path) -> str:
    """Error-message path, e.g. `levels/email-review/level.yaml`."""
    try:
        return str(path.relative_to(root.parent))
    except ValueError:
        return str(path)


class _Resolver:
    """Resolves one difficulty of one level file."""

    def __init__(
        self,
        root: Path,
        level_dir: Path,
        difficulty: str,
        source: str,
        overrides: Mapping[str, str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.level_dir = level_dir.resolve()
        self.difficulty = difficulty
        self.level_file = _display(level_dir / LEVEL_FILE, root)
        self.level_path = str((self.level_dir / LEVEL_FILE).relative_to(self.root))
        self.sources: dict[str, str] = {self.level_path: source}
        self.overrides = overrides or {}

    def fail(self, where: str, problem: str) -> LevelConfigError:
        return LevelConfigError(
            self.level_file,
            f"[{self.difficulty}] {where}: {problem}",
            path=self.level_path,
            line=level_line(
                self.sources[self.level_path], self.difficulty, location_parts(where)
            ),
        )

    def file_error(
        self, path: Path, problem: str, line: int | None = None
    ) -> LevelConfigError:
        return LevelConfigError(
            _display(path, self.root), problem, path=self.relative(path), line=line
        )

    # --- files -------------------------------------------------------------------------------------------

    def path(self, reference: str, where: str) -> Path:
        if Path(reference).is_absolute():
            raise self.fail(where, f"file paths must be relative: {reference}")
        for base in (self.level_dir, self.root):
            candidate = (base / reference).resolve()
            if not candidate.is_relative_to(self.root):
                raise self.fail(
                    where, f"file is outside the levels directory: {reference}"
                )
            relative = str(candidate.relative_to(self.root))
            if relative in self.overrides:
                self.sources.setdefault(relative, self.overrides[relative])
                return candidate
            if candidate.is_file():
                if relative not in self.sources:
                    self.sources[relative] = candidate.read_text(encoding="utf-8")
                return candidate
        raise self.fail(where, f"file not found: {reference}")

    def relative(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def prompt(
        self,
        reference: str,
        where: str,
        values: dict[str, str],
        runtime: frozenset[str] = frozenset(),
    ) -> PromptFile:
        path = self.path(reference, where)
        text = self.sources[self.relative(path)].removesuffix("\n")
        try:
            rendered = render_partial(text, values, runtime)
            kept = list(
                dict.fromkeys(
                    name for name in placeholders(rendered) if name in runtime
                )
            )
        except TemplateError as exc:
            raise self.file_error(
                path,
                f"{exc} (used by {self.level_file} [{self.difficulty}] {where}; "
                f"allowed: {', '.join(sorted({*values, *runtime})) or 'none'})",
                exc.line,
            ) from None
        return PromptFile(file=self.relative(path), text=rendered, runtime=kept)

    def data(
        self, reference: str, where: str, values: dict[str, str]
    ) -> tuple[str, Any]:
        path = self.path(reference, where)
        text = self.sources[self.relative(path)]
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise self.file_error(
                path, f"invalid YAML: {exc}", _yaml_error_line(exc)
            ) from None

        def walk(value: Any) -> Any:
            if isinstance(value, str):
                return render_partial(value, values, TOOL_DATA_RUNTIME)
            if isinstance(value, list):
                return [walk(item) for item in value]
            if isinstance(value, dict):
                return {key: walk(item) for key, item in value.items()}
            return value

        try:
            return self.relative(path), walk(data)
        except TemplateError as exc:
            raise self.file_error(
                path,
                f"{exc} (used by {self.level_file} [{self.difficulty}] {where}; allowed: "
                f"{', '.join(sorted({*values, *TOOL_DATA_RUNTIME}))})",
                _placeholder_line(text, exc.placeholder),
            ) from None

    # --- sections -----------------------------------------------------------------------------------------

    def params(self, model: type[BaseModel], params: dict, where: str) -> BaseModel:
        try:
            return model.model_validate(params)
        except ValidationError as exc:
            raise self.fail(where, _validation_problem(exc)) from None

    def toolset(
        self,
        index: int,
        config: ToolsetConfig,
        values: dict[str, str],
        injection_fields: list[str] | None,
    ) -> ResolvedToolset:
        where = f"agent.tools[{index}]"
        kind = TOOL_KINDS.get(config.kind)
        if kind is None:
            raise self.fail(
                f"{where}.kind",
                f"unknown tool kind '{config.kind}' (known: {', '.join(TOOL_KINDS)})",
            )
        data_file, data = None, None
        if kind.data_model is not None:
            if not config.data:
                raise self.fail(
                    f"{where}.data", f"tool kind '{kind.name}' needs a data file"
                )
            data_file, data = self.data(config.data, f"{where}.data", values)
            try:
                kind.data_model.model_validate(data)
            except ValidationError as exc:
                loc = list(exc.errors(include_url=False)[0]["loc"])
                raise self.file_error(
                    self.root / data_file,
                    _validation_problem(exc),
                    yaml_line(self.sources[data_file], loc),
                ) from None
        elif config.data:
            raise self.fail(
                f"{where}.data", f"tool kind '{kind.name}' takes no data file"
            )
        params = self.params(kind.params_model, config.params, f"{where}.params")
        if unknown := sorted(set(config.descriptions) - set(kind.tool_names)):
            raise self.fail(
                f"{where}.descriptions",
                f"unknown tool(s) {', '.join(unknown)} (kind '{kind.name}' has {', '.join(kind.tool_names)})",
            )
        if data is not None and (problems := kind.check(data, injection_fields)):
            raise self.file_error(self.root / data_file, "; ".join(problems))
        toolset_id = config.id or kind.name
        bound = build_toolset(
            kind,
            toolset_id=toolset_id,
            data=data,
            params=params.model_dump(),
            descriptions=config.descriptions,
            state=ToolState(variables=dict.fromkeys(TOOL_DATA_RUNTIME, "")),
        )
        return ResolvedToolset(
            id=toolset_id,
            kind=kind.name,
            data_file=data_file,
            data=data,
            params=params.model_dump(),
            descriptions=config.descriptions,
            tools=[
                ToolInfo(
                    name=tool.name,
                    description=tool.description,
                    sequential=tool.sequential,
                    parameters=tool.parameters(),
                    toolset=toolset_id,
                    kind=kind.name,
                )
                for tool in bound
            ],
        )

    def guardrail(
        self,
        index: int,
        config: GuardrailConfig,
        order: int,
        tools: dict[str, ToolInfo],
        values: dict[str, str],
    ) -> ResolvedGuardrail:
        where = f"guardrails[{index}]"
        kind = GUARDRAIL_KINDS.get(config.kind)
        if kind is None:
            raise self.fail(
                f"{where}.kind",
                f"unknown guardrail kind '{config.kind}' (known: {', '.join(GUARDRAIL_KINDS)})",
            )
        at, tool = config.at, None
        if at.startswith(AROUND):
            tool = at.removeprefix(AROUND)
            if tool not in tools:
                raise self.fail(f"{where}.at", f"unknown tool '{tool}' in {at}")
        elif at not in FLOW_POSITIONS:
            raise self.fail(
                f"{where}.at",
                f"unknown position '{at}' (use {', '.join(FLOW_POSITIONS)} or around:<tool>)",
            )
        if not kind.allows(at):
            allowed = ", ".join(
                sorted(p if p != AROUND else "around:<tool>" for p in kind.positions)
            )
            raise self.fail(
                f"{where}.at",
                f"guardrail kind '{kind.name}' cannot run at {at} (allowed: {allowed})",
            )
        expected = ON_BLOCK[position_key(at)]
        if config.on_block is not None and config.on_block != expected:
            raise self.fail(f"{where}.on_block", f"must be '{expected}' at {at}")
        model = system_prompt = policy = None
        if kind.uses_model:
            for name in ("model", "system_prompt", "policy"):
                if getattr(config, name) is None:
                    raise self.fail(
                        f"{where}.{name}", f"required for guardrail kind '{kind.name}'"
                    )
            runtime = set(kind.policy_runtime)
            if kind.policy_tool_args and tool:
                runtime |= {f"args.{name}" for name in tools[tool].parameters}
            model = config.model
            system_prompt = self.prompt(
                config.system_prompt, f"{where}.system_prompt", values
            )
            policy = self.prompt(
                config.policy, f"{where}.policy", values, frozenset(runtime)
            )
        else:
            for name in ("model", "system_prompt", "policy"):
                if getattr(config, name) is not None:
                    raise self.fail(
                        f"{where}.{name}", f"not used by guardrail kind '{kind.name}'"
                    )
        if kind.needs_blocked_message and not config.blocked_message:
            raise self.fail(
                f"{where}.blocked_message", f"required for guardrail kind '{kind.name}'"
            )
        return ResolvedGuardrail(
            id=config.id,
            kind=kind.name,
            at=at,
            tool=tool,
            order=order,
            name=config.name or config.id,
            label=config.label or humanize(config.id),
            description=config.description,
            model=model,
            system_prompt=system_prompt,
            policy=policy,
            on_block=expected if kind.name != "sanitizer" else None,
            on_error=config.on_error,
            timeout_seconds=config.timeout_seconds,
            blocked_message=config.blocked_message,
        )

    def resolve(self, variant: LevelVariant, difficulties: list[str]) -> ResolvedLevel:
        surface = variant.attack_surface
        if clash := sorted(
            set(variant.variables)
            & (BUILTIN_VARIABLES | TOOL_DATA_RUNTIME | {"content"})
        ):
            raise self.fail("variables", f"reserved name(s): {', '.join(clash)}")
        values = dict(variant.variables)
        if surface.user_task is not None:
            values["user_task"] = surface.user_task

        secret_kind = SECRET_KINDS.get(variant.secret.kind)
        if secret_kind is None:
            raise self.fail(
                "secret.kind",
                f"unknown secret kind '{variant.secret.kind}' (known: {', '.join(SECRET_KINDS)})",
            )
        self.params(secret_kind.params_model, variant.secret.params, "secret.params")

        # Tools and the injection point.
        agent = variant.agent
        injection_tool = surface.injection.tool
        injection_fields = [field.name for field in surface.injection.fields]
        if injection_tool is not None:
            kinds = [TOOL_KINDS[t.kind] for t in agent.tools if t.kind in TOOL_KINDS]
            owner = next((k for k in kinds if injection_tool in k.tool_names), None)
            if owner is None:
                raise self.fail(
                    "attack_surface.injection.tool", f"unknown tool '{injection_tool}'"
                )
            if owner.injection_tool != injection_tool:
                raise self.fail(
                    "attack_surface.injection.tool",
                    f"tool '{injection_tool}' cannot carry injected content",
                )
        toolsets, tools = [], {}
        for index, config in enumerate(agent.tools):
            kind = TOOL_KINDS.get(config.kind)
            owns = kind is not None and injection_tool in kind.tool_names
            toolset = self.toolset(
                index, config, values, injection_fields if owns else None
            )
            for tool in toolset.tools:
                if tool.name in tools:
                    raise self.fail(
                        f"agent.tools[{index}]", f"duplicate tool name '{tool.name}'"
                    )
                tools[tool.name] = tool
            if toolset.id in {other.id for other in toolsets}:
                raise self.fail(
                    f"agent.tools[{index}].id", f"duplicate toolset id '{toolset.id}'"
                )
            toolsets.append(toolset)
        security = (
            self.prompt(agent.security_prompt, "agent.security_prompt", values)
            if agent.security_prompt
            else None
        )
        system_template = self.prompt(
            agent.system_prompt,
            "agent.system_prompt",
            values,
            frozenset({"security_prompt"}),
        )
        if security and "security_prompt" not in system_template.runtime:
            raise self.file_error(
                self.root / system_template.file,
                f"has no {{{{security_prompt}}}} placeholder but {self.level_file} "
                f"[{self.difficulty}] sets agent.security_prompt",
            )
        system_prompt = render_partial(
            system_template.text, {"security_prompt": security.text if security else ""}
        )

        # Guardrails.
        guardrails = []
        ids = set()
        for index, config in enumerate(variant.guardrails):
            if config.id in ids:
                raise self.fail(
                    f"guardrails[{index}].id", f"duplicate guardrail id '{config.id}'"
                )
            ids.add(config.id)
            order = sum(1 for other in guardrails if other.at == config.at)
            guardrails.append(self.guardrail(index, config, order, tools, values))

        # Success check.
        success_kind = SUCCESS_KINDS.get(variant.success.kind)
        if success_kind is None:
            raise self.fail(
                "success.kind",
                f"unknown success check '{variant.success.kind}' (known: {', '.join(SUCCESS_KINDS)})",
            )
        self.params(success_kind.params_model, variant.success.params, "success.params")
        if success_kind.needs_secret and variant.secret.kind == "none":
            raise self.fail(
                "success.kind",
                f"'{success_kind.name}' needs a secret (secret.kind is 'none')",
            )
        if success_kind.observes == "sent_emails" and not any(
            t.kind == "send_email" for t in toolsets
        ):
            raise self.fail(
                "success.kind", f"'{success_kind.name}' needs a send_email tool"
            )

        # Red team.
        seen = set()
        for index, attack in enumerate(variant.red_team.attacks):
            where = f"red_team.attacks[{index}].type"
            attack_type = ATTACK_TYPES.get(attack.type)
            if attack_type is None:
                raise self.fail(
                    where,
                    f"unknown attack type '{attack.type}' (known: {', '.join(ATTACK_TYPES)})",
                )
            if attack.type in seen:
                raise self.fail(where, f"duplicate attack type '{attack.type}'")
            seen.add(attack.type)
            if attack_type.turns == "multi" and surface.turns != "multi":
                raise self.fail(
                    where,
                    f"'{attack.type}' is a multi-turn attack type but the attack surface is single-turn",
                )

        green = variant.green_team
        return ResolvedLevel(
            id=variant.id,
            title=variant.title,
            description=variant.description,
            order=variant.order,
            difficulty=self.difficulty,
            difficulties=difficulties,
            variables=variant.variables,
            surface=surface,
            secret=variant.secret,
            agent=ResolvedAgent(
                name=agent.name,
                model=agent.model,
                system_prompt=system_prompt,
                system_prompt_file=system_template,
                security_prompt=security,
                toolsets=toolsets,
            ),
            guardrails=guardrails,
            success=variant.success,
            limits=variant.limits,
            refusal=self.prompt(variant.refusal, "refusal", values),
            red_team=variant.red_team,
            green_team=ResolvedGreenTeam(
                generator=green.generator,
                cases=green.cases,
                guidance=self.prompt(green.guidance, "green_team.guidance", values),
            ),
            sources=self.sources,
        )


def load_level_document(
    document: Any,
    level_dir: Path,
    root: Path = LEVELS_ROOT,
    *,
    source: str | None = None,
    overrides: Mapping[str, str] | None = None,
) -> LevelSet:
    """Resolve every difficulty of a level document (parsed YAML) as if it were `<level_dir>/level.yaml`.

    `source` is the raw file text shown in the config pane; in-memory documents are dumped as YAML instead.
    `overrides` replace or add referenced files (path relative to `root` -> content).
    """
    file = _display(level_dir / LEVEL_FILE, root)
    path = str((level_dir / LEVEL_FILE).resolve().relative_to(root.resolve()))
    if source is None:
        source = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)

    def error(problem: str, *parts: str | int) -> LevelConfigError:
        return LevelConfigError(
            file, problem, path=path, line=yaml_line(source, list(parts))
        )

    if not isinstance(document, dict):
        raise error("a level file must be a mapping")
    base = {
        key: value for key, value in document.items() if not str(key).startswith("x-")
    }
    difficulties = base.pop("difficulties", None)
    if not isinstance(difficulties, dict) or not difficulties:
        raise error("difficulties: at least one difficulty is required", "difficulties")
    if base.get("id") != level_dir.name:
        raise error(f"id: must equal the directory name '{level_dir.name}'", "id")
    level = LevelSet(id=level_dir.name, file=file)
    names = [str(name) for name in difficulties]
    for name, override in difficulties.items():
        if override is None:
            override = {}
        if not isinstance(override, dict):
            raise error(
                f"difficulties.{name}: must be a mapping", "difficulties", str(name)
            )
        if "id" in override:
            raise error(
                f"difficulties.{name}.id: a difficulty cannot change the level id",
                "difficulties",
                str(name),
                "id",
            )
        try:
            variant = LevelVariant.model_validate(deep_merge(base, override))
        except ValidationError as exc:
            loc = list(exc.errors(include_url=False)[0]["loc"])
            raise LevelConfigError(
                file,
                _validation_problem(exc, f"[{name}] "),
                path=path,
                line=level_line(source, str(name), loc),
            ) from None
        level.variants[str(name)] = _Resolver(
            root, level_dir, str(name), source, overrides
        ).resolve(variant, names)
    return level


def load_level(
    level_dir: Path,
    root: Path = LEVELS_ROOT,
    overrides: Mapping[str, str] | None = None,
) -> LevelSet:
    """Load `<level_dir>/level.yaml`; `overrides` replace or add files (path relative to `root` -> content)."""
    path = level_dir / LEVEL_FILE
    file = _display(path, root)
    relative = str(path.resolve().relative_to(root.resolve()))
    try:
        if overrides and relative in overrides:
            source = overrides[relative]
        else:
            source = path.read_text(encoding="utf-8")
        document = yaml.safe_load(source)
    except FileNotFoundError:
        raise LevelConfigError(file, "file not found", path=relative) from None
    except yaml.YAMLError as exc:
        raise LevelConfigError(
            file, f"invalid YAML: {exc}", path=relative, line=_yaml_error_line(exc)
        ) from None
    return load_level_document(
        document, level_dir, root, source=source, overrides=overrides
    )


def load_levels(root: Path = LEVELS_ROOT) -> dict[str, LevelSet]:
    """Load every `<root>/*/level.yaml`, ordered by the levels' `order` and id."""
    levels = [
        load_level(path.parent, root) for path in sorted(root.glob(f"*/{LEVEL_FILE}"))
    ]
    levels.sort(key=lambda level: (level.first.order, level.id))
    return {level.id: level for level in levels}
