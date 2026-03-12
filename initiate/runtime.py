from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import PipNetworkConfig, RuntimeConfig, SecurityConfig, load_runtime_config
from .deps import detect_dependencies, normalize_import_to_package, read_python_version_hint
from .lockfile import load_lockfile, parse_freeze_output, write_lockfile

ENV_ACTIVE = "INITIATE_ACTIVE"
ENV_ENTRY = "INITIATE_ENTRY"
ENV_SERVER_IMPORT = "INITIATE_SERVER_IMPORT"

MODULE_NOT_FOUND_RE = re.compile(r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]")
IMPORT_MISMATCH_RE = re.compile(r"ImportError:\s+cannot import name .* from ['\"]([^'\"]+)['\"]")
DIST_NOT_FOUND_RE = re.compile(r"DistributionNotFound:\s+The ['\"]([^'\"]+)['\"] distribution was not found")


@dataclass(frozen=True)
class FrameworkSpec:
    name: str
    app_variable: str = "app"
    explicit_launcher: bool = False


@dataclass
class ManagedEnvironment:
    path: Path
    python_executable: Path
    manifest_path: Path
    dependencies: list[str]
    python_version: str | None


@dataclass(frozen=True)
class RuntimeRecovery:
    reason: str
    packages: list[str]


class FileLock:
    def __init__(self, lock_path: Path, timeout_seconds: int = 120, poll_seconds: float = 0.2) -> None:
        self.lock_path = lock_path
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.fd: int | None = None

    def __enter__(self) -> "FileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        while True:
            try:
                self.fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode("utf-8"))
                return self
            except FileExistsError:
                if time.time() - start > self.timeout_seconds:
                    raise TimeoutError(f"Timed out waiting for lock: {self.lock_path}")
                time.sleep(self.poll_seconds)

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def run(
    script: str | Path | None = None,
    *,
    auto_start: bool = True,
    python_version: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = True,
    retries: int | None = None,
    update: bool | None = None,
    use_lock: bool | None = None,
    strict_lock: bool | None = None,
    auto_lock: bool | None = None,
    script_args: list[str] | None = None,
) -> None:
    if os.environ.get(ENV_SERVER_IMPORT) == "1":
        return

    script_path = _resolve_script_path(script)
    project_root = _discover_project_root(script_path)
    config = load_runtime_config(project_root)
    effective = _with_overrides(
        config,
        retries=retries,
        update=update,
        use_lock=use_lock,
        strict_lock=strict_lock,
        auto_lock=auto_lock,
    )

    if os.environ.get(ENV_ACTIVE) != "1":
        dependencies = sorted(detect_dependencies(script_path, project_root))
        lock_data = load_lockfile(project_root) if effective.use_lock else None
        install_targets = _resolve_install_targets(
            dependencies=dependencies,
            lock_resolved=list(lock_data.resolved) if lock_data else [],
            lock_dependencies=list(lock_data.dependencies) if lock_data else [],
            lock_present=lock_data is not None,
            strict_lock=effective.strict_lock,
            use_lock=effective.use_lock,
        )
        _enforce_security_policy(install_targets, effective.security)

        runtime = RuntimeManager(project_root=project_root, network=effective.network)
        desired_python = python_version or read_python_version_hint(project_root)
        managed_env = runtime.ensure_runtime(
            dependencies=install_targets,
            python_version=desired_python,
            update=effective.update,
        )
        if effective.auto_lock:
            _write_lock_from_environment(
                runtime=runtime,
                managed_env=managed_env,
                project_root=project_root,
                dependencies=dependencies,
            )

        _relaunch_in_managed_runtime(
            runtime=runtime,
            managed_env=managed_env,
            script_path=script_path,
            project_root=project_root,
            retries=max(effective.retries, 0),
            update=effective.update,
            security=effective.security,
            strict_lock=effective.strict_lock,
            script_args=script_args,
        )
        return

    if auto_start and _is_main_script(script_path):
        framework = _detect_framework(script_path, project_root)
        if framework and not framework.explicit_launcher:
            return_code = _launch_framework(
                framework=framework,
                script_path=script_path,
                project_root=project_root,
                host=host,
                port=port,
                reload=reload,
            )
            raise SystemExit(return_code)


def create_lock(
    script: str | Path | None = None,
    *,
    python_version: str | None = None,
    update: bool = True,
) -> Path:
    script_path = _resolve_script_path(script)
    project_root = _discover_project_root(script_path)
    config = load_runtime_config(project_root)
    dependencies = sorted(detect_dependencies(script_path, project_root))
    _enforce_security_policy(dependencies, config.security)

    runtime = RuntimeManager(project_root=project_root, network=config.network)
    desired_python = python_version or read_python_version_hint(project_root)
    managed_env = runtime.ensure_runtime(dependencies=dependencies, python_version=desired_python, update=update)
    return _write_lock_from_environment(
        runtime=runtime,
        managed_env=managed_env,
        project_root=project_root,
        dependencies=dependencies,
    )


def doctor(script: str | Path | None = None) -> dict[str, object]:
    script_path = _resolve_script_path(script) if script else None
    project_root = _discover_project_root(script_path) if script_path else Path.cwd()
    config = load_runtime_config(project_root)
    lock_data = load_lockfile(project_root)

    pip_ok = False
    pip_message = ""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        pip_ok = completed.returncode == 0
        pip_message = (completed.stdout or completed.stderr).strip()
    except Exception as exc:
        pip_message = str(exc)

    cache_root = project_root / ".initiate"
    cache_writable = True
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        cache_writable = False

    detected_dependencies: list[str] = []
    if script_path:
        detected_dependencies = sorted(detect_dependencies(script_path, project_root))

    return {
        "project_root": str(project_root),
        "script": str(script_path) if script_path else None,
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "pip_ok": pip_ok,
        "pip_info": pip_message,
        "uv_available": bool(shutil.which("uv")),
        "py_launcher_available": bool(shutil.which("py")),
        "cache_writable": cache_writable,
        "lockfile_present": bool(lock_data),
        "lockfile_python_version": lock_data.python_version if lock_data else None,
        "detected_dependencies": detected_dependencies,
        "runtime_config": asdict(config),
    }


def clean(project_root: str | Path | None = None, *, remove_lock: bool = False) -> None:
    root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
    cache_dir = root / ".initiate"
    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=False)
    if remove_lock:
        (root / "initiate.lock").unlink(missing_ok=True)


class RuntimeManager:
    def __init__(self, project_root: Path, network: PipNetworkConfig) -> None:
        self.project_root = project_root
        self.cache_root = project_root / ".initiate"
        self.env_root = self.cache_root / "envs"
        self.network = network

    def ensure_runtime(self, dependencies: list[str], python_version: str | None, update: bool = True) -> ManagedEnvironment:
        self.env_root.mkdir(parents=True, exist_ok=True)
        env_name = self._environment_name(dependencies, python_version)
        env_path = self.env_root / env_name
        manifest_path = env_path / ".initiate-manifest.json"

        managed = ManagedEnvironment(
            path=env_path,
            python_executable=_python_in_virtualenv(env_path),
            manifest_path=manifest_path,
            dependencies=list(dependencies),
            python_version=python_version,
        )

        with FileLock(self.cache_root / ".install.lock"):
            if not env_path.exists():
                self._create_environment(env_path, python_version)

            manifest = self._read_manifest(manifest_path)
            desired_manifest = {"dependencies": managed.dependencies, "python_version": python_version}
            if manifest != desired_manifest:
                self.install_packages(
                    python_executable=managed.python_executable,
                    packages=managed.dependencies,
                    upgrade=update,
                )
                self.pip_check(managed.python_executable)
                self.write_manifest(managed)

        return managed

    def install_packages(self, python_executable: Path, packages: list[str], upgrade: bool = True) -> None:
        unique_packages = sorted({pkg for pkg in packages if pkg})
        if not unique_packages:
            return
        _info(f"Installing dependencies: {', '.join(unique_packages)}")
        command = self._pip_install_command(python_executable, upgrade=upgrade)
        command.extend(unique_packages)
        _run_command(command, cwd=self.project_root)

    def pip_check(self, python_executable: Path) -> None:
        _run_command([str(python_executable), "-m", "pip", "check"], cwd=self.project_root)

    def freeze_packages(self, python_executable: Path) -> list[str]:
        result = _run_command(
            [str(python_executable), "-m", "pip", "freeze"],
            cwd=self.project_root,
            capture_output=True,
        )
        return parse_freeze_output(result.stdout)

    def write_manifest(self, managed: ManagedEnvironment) -> None:
        payload = {
            "dependencies": sorted({pkg for pkg in managed.dependencies if pkg}),
            "python_version": managed.python_version,
        }
        managed.manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _pip_install_command(self, python_executable: Path, upgrade: bool) -> list[str]:
        command = [str(python_executable), "-m", "pip", "install", "--disable-pip-version-check"]
        if upgrade:
            command.append("--upgrade")
        if self.network.offline:
            command.append("--no-index")
        if self.network.no_cache:
            command.append("--no-cache-dir")
        if self.network.index_url:
            command.extend(["--index-url", self.network.index_url])
        for item in self.network.extra_index_urls:
            command.extend(["--extra-index-url", item])
        for host in self.network.trusted_hosts:
            command.extend(["--trusted-host", host])
        if self.network.proxy:
            command.extend(["--proxy", self.network.proxy])
        if self.network.cert:
            command.extend(["--cert", self.network.cert])
        if self.network.timeout is not None:
            command.extend(["--timeout", str(self.network.timeout)])
        if self.network.retries is not None:
            command.extend(["--retries", str(self.network.retries)])
        return command

    def _create_environment(self, env_path: Path, python_version: str | None) -> None:
        requested = _normalize_version(python_version) if python_version else None
        uv = shutil.which("uv")
        if requested and uv and not _current_python_matches(requested):
            _info(f"Installing Python {requested} with uv")
            _run_command(["uv", "python", "install", requested], cwd=self.project_root)
            _run_command(["uv", "venv", "--python", requested, str(env_path)], cwd=self.project_root)
            return

        if requested and not _current_python_matches(requested):
            if os.name == "nt" and shutil.which("py"):
                launcher_target = _major_minor(requested)
                _info(f"Creating venv with py launcher: {launcher_target}")
                _run_command(["py", f"-{launcher_target}", "-m", "venv", str(env_path)], cwd=self.project_root)
                return
            _info(
                f"Requested Python {requested} but external version manager is unavailable; "
                "falling back to current interpreter."
            )

        _run_command([sys.executable, "-m", "venv", str(env_path)], cwd=self.project_root)

    def _environment_name(self, dependencies: list[str], python_version: str | None) -> str:
        content = json.dumps({"dependencies": dependencies, "python_version": python_version}, sort_keys=True)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:14]
        version_tag = (python_version or f"{sys.version_info.major}.{sys.version_info.minor}").replace(".", "_")
        return f"py{version_tag}-{digest}"

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, object]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}


def _resolve_script_path(script: str | Path | None) -> Path:
    if script is not None:
        return Path(script).resolve()

    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    if not main_file:
        raise RuntimeError("initiate.run() requires a script path when running interactively.")
    return Path(main_file).resolve()


def _discover_project_root(script_path: Path | None) -> Path:
    if script_path is None:
        return Path.cwd().resolve()
    for candidate in [script_path.parent, *script_path.parents]:
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    return script_path.parent


def _is_main_script(script_path: Path) -> bool:
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    if not main_file:
        return False
    return Path(main_file).resolve() == script_path.resolve()


def _resolve_install_targets(
    dependencies: list[str],
    lock_resolved: list[str],
    lock_dependencies: list[str],
    lock_present: bool,
    strict_lock: bool,
    use_lock: bool,
) -> list[str]:
    if strict_lock:
        if lock_resolved:
            return sorted({item for item in lock_resolved if item})
        if lock_dependencies:
            return sorted({item for item in lock_dependencies if item})
        if lock_present:
            return []
        raise RuntimeError("strict-lock enabled but initiate.lock was not found.")

    install_targets = set(dependencies)
    if use_lock:
        install_targets.update(lock_dependencies)
    return sorted(install_targets)


def _relaunch_in_managed_runtime(
    runtime: RuntimeManager,
    managed_env: ManagedEnvironment,
    script_path: Path,
    project_root: Path,
    retries: int,
    update: bool,
    security: SecurityConfig,
    strict_lock: bool,
    script_args: list[str] | None = None,
) -> None:
    env = os.environ.copy()
    env[ENV_ACTIVE] = "1"
    env[ENV_ENTRY] = str(script_path)
    env["PYTHONNOUSERSITE"] = "1"
    _inject_pythonpath(env, [_runtime_source_root(), project_root])
    forwarded_args = list(script_args) if script_args is not None else list(sys.argv[1:])
    command = [str(managed_env.python_executable), str(script_path), *forwarded_args]
    _info(f"Re-launching in managed runtime: {' '.join(command)}")

    for attempt in range(retries + 1):
        result = subprocess.run(
            command,
            cwd=project_root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode == 0:
            raise SystemExit(0)

        recovery = _infer_runtime_recovery(result.stderr or "")
        if not recovery:
            raise SystemExit(result.returncode)
        if attempt >= retries:
            _info("Retry limit reached while trying to auto-heal dependency issues.")
            raise SystemExit(result.returncode)

        packages = sorted({normalize_import_to_package(pkg) for pkg in recovery.packages if pkg})
        if not packages:
            raise SystemExit(result.returncode)
        if strict_lock:
            _info("strict-lock is enabled; auto-heal installation is blocked.")
            raise SystemExit(result.returncode)

        _enforce_security_policy(packages, security)

        _info(
            f"Auto-heal detected '{recovery.reason}'. "
            f"Installing/updating {', '.join(packages)} (attempt {attempt + 1}/{retries})."
        )
        runtime.install_packages(
            python_executable=managed_env.python_executable,
            packages=packages,
            upgrade=update,
        )
        runtime.pip_check(managed_env.python_executable)
        managed_env.dependencies = sorted(set(managed_env.dependencies) | set(packages))
        runtime.write_manifest(managed_env)

    raise SystemExit(1)


def _infer_runtime_recovery(stderr: str) -> RuntimeRecovery | None:
    missing_modules = [match.split(".", 1)[0] for match in MODULE_NOT_FOUND_RE.findall(stderr)]
    if missing_modules:
        return RuntimeRecovery(reason="module_not_found", packages=missing_modules)

    mismatched = [match.split(".", 1)[0] for match in IMPORT_MISMATCH_RE.findall(stderr)]
    if mismatched:
        return RuntimeRecovery(reason="import_mismatch", packages=mismatched)

    missing_dists = DIST_NOT_FOUND_RE.findall(stderr)
    if missing_dists:
        return RuntimeRecovery(reason="distribution_not_found", packages=missing_dists)

    return None


def _detect_framework(script_path: Path, project_root: Path) -> FrameworkSpec | None:
    try:
        tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
    except (UnicodeDecodeError, SyntaxError):
        return None

    imports: set[str] = set()
    app_var = "app"
    explicit_launcher = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".", 1)[0])
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    call_name = _call_name(node.value.func)
                    if call_name.endswith("FastAPI") and target.id == "app":
                        app_var = "app"
                    if call_name.endswith("Flask") and target.id == "app":
                        app_var = "app"
                    if call_name.endswith("Dash") and target.id == "app":
                        app_var = "app"
        if isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in {"uvicorn.run", "app.run", "flask.run", "demo.launch", "manage.execute_from_command_line"}:
                explicit_launcher = True

    if "fastapi" in imports:
        return FrameworkSpec(name="fastapi", app_variable=app_var, explicit_launcher=explicit_launcher)
    if "flask" in imports:
        return FrameworkSpec(name="flask", app_variable=app_var, explicit_launcher=explicit_launcher)
    if "streamlit" in imports:
        return FrameworkSpec(name="streamlit", explicit_launcher=explicit_launcher)
    if "django" in imports and (project_root / "manage.py").exists() and script_path.name != "manage.py":
        return FrameworkSpec(name="django", explicit_launcher=explicit_launcher)
    if "gradio" in imports:
        return FrameworkSpec(name="gradio", explicit_launcher=True)
    if "dash" in imports:
        return FrameworkSpec(name="dash", explicit_launcher=True)
    return None


def _launch_framework(
    framework: FrameworkSpec,
    script_path: Path,
    project_root: Path,
    host: str,
    port: int,
    reload: bool,
) -> int:
    python_executable = Path(sys.executable)
    env = os.environ.copy()
    env[ENV_SERVER_IMPORT] = "1"

    if framework.name == "fastapi":
        module_path = _module_path(script_path, project_root)
        if not module_path:
            _info("Could not infer module path for FastAPI app; continuing script execution.")
            return 0
        command = [
            str(python_executable),
            "-m",
            "uvicorn",
            f"{module_path}:{framework.app_variable}",
            "--host",
            host,
            "--port",
            str(port),
        ]
        if reload:
            command.append("--reload")
        _info(f"Starting FastAPI server on {host}:{port}")
        return subprocess.run(command, cwd=project_root, env=env, check=False).returncode

    if framework.name == "flask":
        module_path = _module_path(script_path, project_root)
        if not module_path:
            _info("Could not infer module path for Flask app; continuing script execution.")
            return 0
        command = [
            str(python_executable),
            "-m",
            "flask",
            "--app",
            f"{module_path}:{framework.app_variable}",
            "run",
            "--host",
            host,
            "--port",
            str(port),
        ]
        _info(f"Starting Flask server on {host}:{port}")
        return subprocess.run(command, cwd=project_root, env=env, check=False).returncode

    if framework.name == "streamlit":
        command = [str(python_executable), "-m", "streamlit", "run", str(script_path)]
        _info(f"Starting Streamlit app: {script_path.name}")
        return subprocess.run(command, cwd=project_root, env=env, check=False).returncode

    if framework.name == "django":
        manage_py = project_root / "manage.py"
        command = [str(python_executable), str(manage_py), "runserver", f"{host}:{port}"]
        _info(f"Starting Django server on {host}:{port}")
        return subprocess.run(command, cwd=project_root, env=env, check=False).returncode

    return 0


def _module_path(script_path: Path, project_root: Path) -> str | None:
    try:
        relative = script_path.resolve().relative_to(project_root.resolve()).with_suffix("")
    except ValueError:
        relative = script_path.with_suffix("")
    parts = list(relative.parts)
    if not parts:
        return None
    if any(not re.match(r"^[A-Za-z_]\w*$", part) for part in parts):
        return None
    return ".".join(parts)


def _python_in_virtualenv(env_path: Path) -> Path:
    if os.name == "nt":
        return env_path / "Scripts" / "python.exe"
    return env_path / "bin" / "python"


def _current_python_matches(version_spec: str) -> bool:
    requested = _normalize_version(version_spec)
    current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    current_norm = _normalize_version(current)
    return current_norm.startswith(requested)


def _normalize_version(version_spec: str | None) -> str:
    if not version_spec:
        return ""
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", version_spec)
    return match.group(1) if match else version_spec.strip()


def _major_minor(version_spec: str) -> str:
    normalized = _normalize_version(version_spec)
    parts = normalized.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return normalized


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _call_name(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    return ""


def _run_command(
    command: list[str],
    cwd: Path,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=capture_output,
        text=True,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        error_text = stderr or stdout
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}\n{error_text}")
    return completed


def _info(message: str) -> None:
    print(f"[initiate] {message}", file=sys.stderr)


def _runtime_source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _inject_pythonpath(env: dict[str, str], paths: list[Path]) -> None:
    existing = env.get("PYTHONPATH", "")
    merged: list[str] = []
    for path in paths:
        value = str(path.resolve())
        if value:
            merged.append(value)
    if existing:
        merged.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(merged)


def _with_overrides(
    config: RuntimeConfig,
    *,
    retries: int | None,
    update: bool | None,
    use_lock: bool | None,
    strict_lock: bool | None,
    auto_lock: bool | None,
) -> RuntimeConfig:
    return RuntimeConfig(
        retries=config.retries if retries is None else retries,
        update=config.update if update is None else update,
        use_lock=config.use_lock if use_lock is None else use_lock,
        strict_lock=config.strict_lock if strict_lock is None else strict_lock,
        auto_lock=config.auto_lock if auto_lock is None else auto_lock,
        network=config.network,
        security=config.security,
    )


def _package_root(spec: str) -> str:
    token = re.split(r"[<>=!~;\[\s]", spec, maxsplit=1)[0].strip()
    return token.lower().replace("_", "-")


def _enforce_security_policy(packages: list[str], security: SecurityConfig) -> None:
    blocked = {item.lower().replace("_", "-") for item in security.blocked_packages}
    trusted = {item.lower().replace("_", "-") for item in security.trusted_packages}
    roots = {_package_root(spec) for spec in packages if spec}

    blocked_found = sorted(root for root in roots if root in blocked)
    if blocked_found:
        raise RuntimeError(f"Blocked dependencies detected: {', '.join(blocked_found)}")

    if security.enforce_trusted and trusted:
        untrusted = sorted(root for root in roots if root not in trusted)
        if untrusted:
            raise RuntimeError(
                "Untrusted dependencies detected while enforce-trusted=true: " + ", ".join(untrusted)
            )


def _write_lock_from_environment(
    runtime: RuntimeManager,
    managed_env: ManagedEnvironment,
    project_root: Path,
    dependencies: list[str],
) -> Path:
    resolved = runtime.freeze_packages(managed_env.python_executable)
    python_version_result = _run_command(
        [str(managed_env.python_executable), "-c", "import sys; print(sys.version.split()[0])"],
        cwd=project_root,
        capture_output=True,
    )
    lock_path = write_lockfile(
        project_root=project_root,
        python_version=(python_version_result.stdout or "").strip() or sys.version.split()[0],
        dependencies=dependencies,
        resolved=resolved,
    )
    _info(f"Updated lockfile: {lock_path}")
    return lock_path
