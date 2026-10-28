"""Rules for level files edited in the UI (overrides on top of `backend/levels/`).

A level's overrides may replace its `level.yaml` and the files it references inside its own directory, and add
new files there that the edited `level.yaml` references in the same save. Files in `shared/` (used by every
level) and in other levels' directories stay read-only. Every path is relative to the levels root, like the
`source.files[].path` of the level view.
"""

from pathlib import Path, PurePosixPath
from typing import Any

from hackathon.arena.config.loader import LEVEL_FILE, LevelConfigError, LevelSet

SHARED_DIR = "shared"
MAX_FILES = 50
MAX_FILE_CHARACTERS = 500_000


class SourceErrors(Exception):
    """A save was rejected; `errors` are `{path, message, line?}` items for the 422 response."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(f"{e['path']}: {e['message']}" for e in errors))
        self.errors = errors


def problem(path: str, message: str, line: int | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"path": path, "message": message}
    if line is not None:
        item["line"] = line
    return item


def config_problem(error: LevelConfigError) -> dict[str, Any]:
    """A loader error as a 422 item: the file relative to the levels root, the problem and its line."""
    return problem(error.path, error.problem, error.line)


def level_file(level_id: str) -> str:
    return f"{level_id}/{LEVEL_FILE}"


def is_editable(level_id: str, path: str) -> bool:
    """Whether `path` (relative to the levels root) lies in the level's own directory."""
    return path.startswith(f"{level_id}/") and not path.startswith(f"{SHARED_DIR}/")


def check_path(level_id: str, path: str, root: Path) -> str | None:
    """Why `path` cannot be saved as an override of `level_id`, or `None`."""
    if not path:
        return "a file path is required"
    if "\\" in path or "\x00" in path:
        return "file paths use '/' and no special characters"
    if path.startswith("/") or PurePosixPath(path).is_absolute():
        return "file paths must be relative to backend/levels/"
    parts = path.split("/")
    if ".." in parts:
        return "'..' is not allowed in file paths"
    if any(part in ("", ".") for part in parts):
        return "file paths must not contain empty or '.' segments"
    if parts[0] == SHARED_DIR:
        return "files in shared/ are read-only: they are used by every level"
    if parts[0] != level_id or len(parts) < 2:
        return f"only files inside {level_id}/ can be edited with this level"
    level_dir = (root / level_id).resolve()
    if not (root / path).resolve().is_relative_to(level_dir):
        return f"the file resolves outside {level_id}/"
    return None


def referenced_files(level: LevelSet) -> dict[str, str]:
    """Every file a level loads (any difficulty), path -> content, `level.yaml` first."""
    files: dict[str, str] = {}
    for variant in level.variants.values():
        for path, content in variant.sources.items():
            files.setdefault(path, content)
    return files


def repository_content(root: Path, path: str) -> str | None:
    file = root / path
    return file.read_text(encoding="utf-8") if file.is_file() else None
