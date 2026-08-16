"""Project file serialization."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

import yaml

from .models import Project


class ProjectFileError(ValueError):
    """Raised when a project file cannot be read or decoded."""


def load_document(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectFileError(f"cannot read project {path}: {exc}") from exc
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(text)
        else:
            value = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ProjectFileError(f"cannot parse project {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectFileError(f"project {path} must contain a mapping")
    return value


def load_project(path: str | Path) -> Project:
    path = Path(path)
    return Project.from_dict(load_document(path), source_path=str(path))


def save_project(project: Project, path: str | Path) -> None:
    """Serialize a project and atomically replace its destination file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = project.to_dict()
    try:
        if path.suffix.lower() == ".json":
            text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        else:
            text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectFileError(f"cannot write project {path}: {exc}") from exc
