"""Load trusted, repository-owned level definitions from TOML files."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


CONFIG_DIR = Path(os.environ.get("LEVEL_CONFIG_DIR", Path(__file__).resolve().parents[1] / "configs"))


class ToolConfig(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    entrypoint: str
    description: str
    group: str = "Tools"

    @field_validator("entrypoint")
    @classmethod
    def valid_entrypoint(cls, value: str) -> str:
        if ":" not in value:
            raise ValueError("entrypoint must be module:function")
        return value


class GuardrailConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    type: Literal["llm_judge", "custom"]
    hook: Literal["input", "before_tool", "after_tool", "output"]
    prompt: str | None = None
    entrypoint: str | None = None
    model: str = "gemini-3.8-flash"
    tool: str | None = None
    blocked_message: str = "Request blocked by guardrail."
    settings: dict = Field(default_factory=dict)

    @field_validator("entrypoint")
    @classmethod
    def valid_entrypoint(cls, value: str | None) -> str | None:
        if value is not None and ":" not in value:
            raise ValueError("entrypoint must be module:function")
        return value


class LevelConfig(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    title: str
    description: str = ""
    model: str = "gemini-3.8-flash"
    system_prompt: str
    data_source: str | None = None
    secret_label: str | None = None
    secret_provider: str | None = None
    tools: list[ToolConfig] = Field(default_factory=list)
    guardrails: list[GuardrailConfig] = Field(default_factory=list)
    evaluator: str
    goal: str
    attacker: dict = Field(default_factory=dict)
    objective: dict = Field(default_factory=dict)
    scan: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_components(self):
        tool_names = [tool.name for tool in self.tools]
        guard_ids = [guard.id for guard in self.guardrails]
        if len(tool_names) != len(set(tool_names)) or len(guard_ids) != len(set(guard_ids)):
            raise ValueError("Tool names and guardrail IDs must be unique")
        for guard in self.guardrails:
            if guard.type == "llm_judge" and not guard.prompt:
                raise ValueError(f"LLM guardrail {guard.id} needs a prompt")
            if guard.type == "custom" and not guard.entrypoint:
                raise ValueError(f"Custom guardrail {guard.id} needs an entrypoint")
            if guard.tool and guard.tool not in tool_names:
                raise ValueError(f"Guardrail {guard.id} references unknown tool {guard.tool}")
            if guard.hook in ("before_tool", "after_tool") and not tool_names:
                raise ValueError(f"Tool guardrail {guard.id} requires at least one tool")
        return self

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "model": self.model,
            "data_source": self.data_source,
            "secret_label": self.secret_label,
            "tools": [tool.model_dump(exclude={"entrypoint"}) for tool in self.tools],
            "guardrails": [
                guardrail.model_dump(exclude={"entrypoint", "prompt", "settings"})
                for guardrail in self.guardrails
            ],
        }


def _merged(source: dict, override: dict) -> dict:
    result = {key: value for key, value in source.items() if key != "abstract"}
    for key, value in override.items():
        if key in ("extends", "abstract"):
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merged(result[key], value)
        else:
            result[key] = value
    return result


def _read(path: Path, seen: frozenset[Path] = frozenset()) -> dict:
    path = path.resolve()
    if path.parent != CONFIG_DIR.resolve() or path.suffix != ".toml":
        raise ValueError("Level config must be a TOML file in the config directory")
    if path in seen:
        raise ValueError("Circular level config inheritance")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    parent = data.get("extends")
    if parent:
        if not isinstance(parent, str):
            raise ValueError("extends must name a TOML file")
        data = _merged(_read(CONFIG_DIR / parent, seen | {path}), data)
    return data


def get_level(level_id: str) -> LevelConfig:
    if not level_id or not all(char.isalnum() or char in "_-" for char in level_id):
        raise ValueError("Invalid level ID")
    path = CONFIG_DIR / f"{level_id}.toml"
    if not path.is_file():
        raise ValueError(f"Unknown level: {level_id}")
    data = _read(path)
    if data.get("abstract") or data.get("id") != level_id:
        raise ValueError(f"Invalid level config: {level_id}")
    return LevelConfig.model_validate(data)


def list_levels() -> list[LevelConfig]:
    levels = []
    for path in sorted(CONFIG_DIR.glob("*.toml")):
        if path.stem.startswith("_"):
            continue
        levels.append(get_level(path.stem))
    return levels
