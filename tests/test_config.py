from __future__ import annotations

from pathlib import Path

from initiate.config import load_runtime_config
from initiate.runtime import doctor


def test_load_runtime_config_from_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.initiate]
retries = 7
update = false
strict-lock = true
trusted-packages = ["fastapi", "uvicorn"]
blocked-packages = ["evilpkg"]
index-url = "https://example.org/simple"
offline = true
""".strip(),
        encoding="utf-8",
    )

    config = load_runtime_config(tmp_path)
    assert config.retries == 7
    assert config.update is False
    assert config.strict_lock is True
    assert config.network.offline is True
    assert config.network.index_url == "https://example.org/simple"
    assert "fastapi" in config.security.trusted_packages
    assert "evilpkg" in config.security.blocked_packages


def test_doctor_returns_expected_keys(tmp_path: Path) -> None:
    script = tmp_path / "app.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    data = doctor(script)
    assert "pip_ok" in data
    assert "runtime_config" in data
    assert data["script"] == str(script.resolve())
