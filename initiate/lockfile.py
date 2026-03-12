from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LOCKFILE_NAME = "initiate.lock"
LOCKFILE_VERSION = 1


@dataclass(frozen=True)
class LockfileData:
    version: int
    python_version: str
    generated_at: str
    dependencies: tuple[str, ...]
    resolved: tuple[str, ...]


def lockfile_path(project_root: Path) -> Path:
    return project_root / LOCKFILE_NAME


def load_lockfile(project_root: Path) -> LockfileData | None:
    path = lockfile_path(project_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return LockfileData(
        version=_safe_int(payload.get("version"), LOCKFILE_VERSION),
        python_version=str(payload.get("python_version", "")),
        generated_at=str(payload.get("generated_at", "")),
        dependencies=tuple(_safe_str_list(payload.get("dependencies"))),
        resolved=tuple(_safe_str_list(payload.get("resolved"))),
    )


def write_lockfile(project_root: Path, python_version: str, dependencies: list[str], resolved: list[str]) -> Path:
    payload = {
        "version": LOCKFILE_VERSION,
        "python_version": python_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dependencies": sorted({item for item in dependencies if item}),
        "resolved": sorted({item for item in resolved if item}),
    }
    path = lockfile_path(project_root)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def parse_freeze_output(output: str) -> list[str]:
    entries: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-e "):
            continue
        entries.append(line)
    return entries


def _safe_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _safe_int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default
