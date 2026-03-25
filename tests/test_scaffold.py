from __future__ import annotations

from pathlib import Path

import pytest

from initiate.scaffold import init_project


def test_init_project_creates_script_template(tmp_path: Path) -> None:
    created = init_project(tmp_path / "demo", project_type="script", entry_file="app.py")
    assert len(created) == 3
    app_file = tmp_path / "demo" / "app.py"
    assert app_file.exists()
    assert "initiate.run()" in app_file.read_text(encoding="utf-8")


def test_init_project_refuses_overwrite_without_force(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir(parents=True, exist_ok=True)
    (target / "app.py").write_text("print('existing')\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        init_project(target, project_type="script", entry_file="app.py", force=False)
