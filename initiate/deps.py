from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

STD_LIBS = set(getattr(sys, "stdlib_module_names", ())) | set(sys.builtin_module_names)
SKIP_DIRS = {".git", ".hg", ".svn", ".idea", ".vscode", "__pycache__", ".venv", "venv", ".initiate"}

PACKAGE_NAME_MAP = {
    "cv2": "opencv-python",
    "pil": "Pillow",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "seaboard": "seaborn",
}


def detect_dependencies(entry_script: Path, project_root: Path) -> set[str]:
    visited: set[Path] = set()
    queue = [entry_script.resolve()]
    dependencies: set[str] = set()

    while queue:
        current = queue.pop()
        if current in visited or not current.exists() or current.suffix != ".py":
            continue
        visited.add(current)

        try:
            tree = ast.parse(current.read_text(encoding="utf-8"), filename=str(current))
        except (UnicodeDecodeError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name
                    local_target = _resolve_absolute_module(module_name, project_root)
                    if local_target:
                        queue.append(local_target)
                    else:
                        _add_dependency(dependencies, module_name)

            if isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    relative_base = _resolve_relative_module(current, project_root, node.module, node.level)
                    if relative_base:
                        queue.append(relative_base)
                    if node.module is None:
                        for alias in node.names:
                            rel_alias = _resolve_relative_module(current, project_root, alias.name, node.level)
                            if rel_alias:
                                queue.append(rel_alias)
                    continue

                if node.module:
                    local_target = _resolve_absolute_module(node.module, project_root)
                    if local_target:
                        queue.append(local_target)
                        for alias in node.names:
                            submodule = _resolve_absolute_module(f"{node.module}.{alias.name}", project_root)
                            if submodule:
                                queue.append(submodule)
                    else:
                        _add_dependency(dependencies, node.module)

    dependencies.update(load_declared_dependencies(project_root))
    return dependencies


def load_declared_dependencies(project_root: Path) -> set[str]:
    dependencies: set[str] = set()
    for name in ("requirements.txt", "requirements-dev.txt"):
        path = project_root / name
        if path.exists():
            dependencies.update(_parse_requirements_txt(path))

    for name in ("requirements.toml", "pyproject.toml"):
        path = project_root / name
        if path.exists():
            dependencies.update(_parse_requirements_toml(path))

    for name in ("requirements.yaml", "requirements.yml", "initiate.yaml", "initiate.yml"):
        path = project_root / name
        if path.exists():
            dependencies.update(_parse_requirements_yaml(path))

    return {dep for dep in dependencies if dep}


def read_python_version_hint(project_root: Path) -> str | None:
    python_version_file = project_root / ".python-version"
    if python_version_file.exists():
        value = python_version_file.read_text(encoding="utf-8").strip()
        if value:
            return value.split()[0]

    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(encoding="utf-8")
        if tomllib:
            try:
                data = tomllib.loads(content)
            except Exception:
                data = {}
            requires_python = data.get("project", {}).get("requires-python")
            if isinstance(requires_python, str):
                version = _extract_python_version(requires_python)
                if version:
                    return version
        else:  # pragma: no cover
            match = re.search(r"requires-python\s*=\s*['\"]([^'\"]+)['\"]", content)
            if match:
                version = _extract_python_version(match.group(1))
                if version:
                    return version

    return None


def _parse_requirements_txt(path: Path) -> set[str]:
    result: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", "--")):
            continue
        line = line.split(" #", 1)[0].strip()
        if line:
            result.add(line)
    return result


def _parse_requirements_toml(path: Path) -> set[str]:
    if not tomllib:
        return set()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()

    deps: set[str] = set()
    project = data.get("project", {})
    deps.update(_to_str_set(project.get("dependencies", [])))

    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for values in optional.values():
            deps.update(_to_str_set(values))

    tool_deps = data.get("tool", {}).get("initiate", {}).get("dependencies")
    deps.update(_to_str_set(tool_deps or []))
    deps.update(_to_str_set(data.get("initiate", {}).get("dependencies", [])))
    return deps


def _parse_requirements_yaml(path: Path) -> set[str]:
    deps: set[str] = set()
    in_dependencies = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^(dependencies|packages)\s*:\s*$", line):
            in_dependencies = True
            continue
        if in_dependencies and line.startswith("- "):
            value = line[2:].strip().strip("\"'")
            if value:
                deps.add(value)
            continue
        if in_dependencies and not line.startswith("- "):
            in_dependencies = False
    return deps


def _resolve_relative_module(current_file: Path, project_root: Path, module: str | None, level: int) -> Path | None:
    base = current_file.parent
    for _ in range(max(level - 1, 0)):
        base = base.parent
        if not str(base).startswith(str(project_root)):
            return None

    if module:
        base = base.joinpath(*module.split("."))

    module_file = base.with_suffix(".py")
    if module_file.exists():
        return module_file.resolve()

    init_file = base / "__init__.py"
    if init_file.exists():
        return init_file.resolve()

    return None


def _resolve_absolute_module(module: str, project_root: Path) -> Path | None:
    if not module:
        return None
    top = module.split(".", 1)[0]
    if top in SKIP_DIRS:
        return None

    for root in (project_root, project_root / "src"):
        path = root.joinpath(*module.split("."))
        file_candidate = path.with_suffix(".py")
        if file_candidate.exists():
            return file_candidate.resolve()
        init_candidate = path / "__init__.py"
        if init_candidate.exists():
            return init_candidate.resolve()
    return None


def _add_dependency(dependencies: set[str], module_name: str) -> None:
    top = module_name.split(".", 1)[0]
    if not top or top in STD_LIBS or top == "initiate":
        return
    dependencies.add(normalize_import_to_package(top))


def normalize_import_to_package(module_name: str) -> str:
    top = module_name.split(".", 1)[0]
    return PACKAGE_NAME_MAP.get(top, top)


def _to_str_set(value: object) -> set[str]:
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def _extract_python_version(spec: str) -> str | None:
    normalized = spec.strip()
    if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", normalized):
        return normalized
    match = re.fullmatch(r"(==|~=)\s*(\d+\.\d+(?:\.\d+)?)", normalized)
    return match.group(2) if match else None
