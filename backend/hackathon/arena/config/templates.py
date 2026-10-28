"""`{{placeholder}}` templates used in level prompt and data files.

Placeholders use double braces so prompt text with literal `{…}` (JSON examples) stays safe. Two kinds exist:

- load-time placeholders (level `variables`, `security_prompt`, `user_task`) are rendered by the loader;
- runtime placeholders (e.g. `secret`, `content`, `args.to`) are validated by the loader, kept in the text and
  rendered by the engine with `render()` once the value is known.
"""

import re
from collections.abc import Collection, Mapping
from typing import Any

PLACEHOLDER = re.compile(
    r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*\}\}"
)


class TemplateError(ValueError):
    """A template references an unknown or malformed placeholder; `line` (1-based) is where, if known."""

    def __init__(
        self, message: str, line: int | None = None, placeholder: str | None = None
    ) -> None:
        super().__init__(message)
        self.line = line
        self.placeholder = placeholder


def _line_at(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def placeholders(text: str) -> list[str]:
    """Return the placeholder names in `text`, in order of appearance (with repeats)."""
    names = [match.group(1) for match in PLACEHOLDER.finditer(text)]
    rest = PLACEHOLDER.sub("", text)
    if "{{" in rest or "}}" in rest:
        lines = rest.splitlines()
        number, line = next(
            (n, line) for n, line in enumerate(lines, 1) if "{{" in line or "}}" in line
        )
        raise TemplateError(f"malformed placeholder near {line.strip()[:60]!r}", number)
    return names


def render_partial(
    text: str, values: Mapping[str, str], runtime: Collection[str] = ()
) -> str:
    """Substitute `values`; keep `runtime` placeholders; reject everything else.

    Substitution is a single pass, so substituted text is never scanned again.
    """
    placeholders(text)  # malformed placeholders
    for match in PLACEHOLDER.finditer(text):
        name = match.group(1)
        if name not in values and name not in runtime:
            raise TemplateError(
                f"unknown placeholder {{{{{name}}}}}",
                _line_at(text, match.start()),
                name,
            )

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return values[name] if name in values else match.group(0)

    return PLACEHOLDER.sub(replace, text)


def render(text: str, values: Mapping[str, Any]) -> str:
    """Render every placeholder in `text`; a missing value is an error (runtime use)."""
    missing = sorted({name for name in placeholders(text) if name not in values})
    if missing:
        raise TemplateError(
            "missing value for " + ", ".join(f"{{{{{name}}}}}" for name in missing)
        )
    return PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), text)


def render_data(value: Any, values: Mapping[str, Any]) -> Any:
    """Render runtime placeholders in every string of a YAML-like data structure."""
    if isinstance(value, str):
        return render(value, values) if "{{" in value else value
    if isinstance(value, list):
        return [render_data(item, values) for item in value]
    if isinstance(value, dict):
        return {key: render_data(item, values) for key, item in value.items()}
    return value
