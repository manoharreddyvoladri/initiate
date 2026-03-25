from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from initiate.config import SecurityConfig
from initiate.runtime import (
    ManagedEnvironment,
    RuntimeRecovery,
    _enforce_security_policy,
    _friendly_runtime_help,
    _infer_runtime_recovery,
    _relaunch_in_managed_runtime,
    _resolve_install_targets,
)


def test_infer_runtime_recovery_for_missing_module() -> None:
    trace = "ModuleNotFoundError: No module named 'pandas'"
    recovery = _infer_runtime_recovery(trace)
    assert recovery == RuntimeRecovery(reason="module_not_found", packages=["pandas"])


def test_infer_runtime_recovery_for_import_mismatch() -> None:
    trace = "ImportError: cannot import name 'BaseModel' from 'pydantic'"
    recovery = _infer_runtime_recovery(trace)
    assert recovery == RuntimeRecovery(reason="import_mismatch", packages=["pydantic"])


def test_relaunch_auto_heals_missing_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path
    script_path = project_root / "app.py"
    script_path.write_text("print('hello')\n", encoding="utf-8")
    python_executable = project_root / ".initiate" / "envs" / "py3x" / "bin" / "python"
    managed = ManagedEnvironment(
        path=python_executable.parent.parent,
        python_executable=python_executable,
        manifest_path=project_root / ".initiate-manifest.json",
        dependencies=[],
        python_version=None,
    )

    class DummyRuntime:
        def __init__(self) -> None:
            self.installs: list[tuple[list[str], bool]] = []
            self.manifest_writes = 0
            self.checks = 0

        def install_packages(self, python_executable: Path, packages: list[str], upgrade: bool = True) -> None:
            self.installs.append((packages, upgrade))

        def pip_check(self, python_executable: Path) -> None:
            self.checks += 1

        def write_manifest(self, managed_env: ManagedEnvironment) -> None:
            self.manifest_writes += 1

    runtime = DummyRuntime()
    calls: list[int] = []
    commands: list[list[str]] = []

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        if args:
            commands.append(list(args[0]))
        del kwargs
        calls.append(1)
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="ModuleNotFoundError: No module named 'seaborn'",
            )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc:
        _relaunch_in_managed_runtime(
            runtime=runtime,  # type: ignore[arg-type]
            managed_env=managed,
            script_path=script_path,
            project_root=project_root,
            retries=2,
            update=True,
            security=SecurityConfig(),
            strict_lock=False,
            script_args=["--flag", "1"],
        )

    assert exc.value.code == 0
    assert runtime.installs == [(["seaborn"], True)]
    assert runtime.manifest_writes == 1
    assert runtime.checks == 1
    assert commands
    assert commands[0][-2:] == ["--flag", "1"]


def test_security_policy_blocks_packages() -> None:
    with pytest.raises(RuntimeError):
        _enforce_security_policy(
            ["requests", "numpy"],
            SecurityConfig(blocked_packages=("requests",)),
        )


def test_resolve_install_targets_strict_lock_uses_resolved() -> None:
    targets = _resolve_install_targets(
        dependencies=["fastapi"],
        lock_resolved=["fastapi==0.116.0", "uvicorn==0.31.0"],
        lock_dependencies=["fastapi", "uvicorn"],
        lock_present=True,
        strict_lock=True,
        use_lock=True,
    )
    assert targets == ["fastapi==0.116.0", "uvicorn==0.31.0"]


def test_resolve_install_targets_strict_lock_requires_lock() -> None:
    with pytest.raises(RuntimeError):
        _resolve_install_targets(
            dependencies=["fastapi"],
            lock_resolved=[],
            lock_dependencies=[],
            lock_present=False,
            strict_lock=True,
            use_lock=True,
        )


def test_resolve_install_targets_strict_lock_allows_empty_lock() -> None:
    targets = _resolve_install_targets(
        dependencies=["fastapi"],
        lock_resolved=[],
        lock_dependencies=[],
        lock_present=True,
        strict_lock=True,
        use_lock=True,
    )
    assert targets == []


def test_friendly_runtime_help_for_syntax_error() -> None:
    message = _friendly_runtime_help("SyntaxError: invalid syntax", return_code=1)
    assert "syntax error" in message.lower()
