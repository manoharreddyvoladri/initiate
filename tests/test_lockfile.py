from __future__ import annotations

from pathlib import Path

from initiate.lockfile import load_lockfile, parse_freeze_output, write_lockfile


def test_lockfile_roundtrip(tmp_path: Path) -> None:
    write_lockfile(
        project_root=tmp_path,
        python_version="3.11.7",
        dependencies=["fastapi", "uvicorn"],
        resolved=["fastapi==0.116.0", "uvicorn==0.31.0"],
    )
    data = load_lockfile(tmp_path)
    assert data is not None
    assert data.python_version == "3.11.7"
    assert "fastapi" in data.dependencies
    assert "fastapi==0.116.0" in data.resolved


def test_parse_freeze_output_filters_editable() -> None:
    output = """
# comment
fastapi==0.116.0
-e git+https://example.com/repo
uvicorn==0.31.0
"""
    values = parse_freeze_output(output)
    assert values == ["fastapi==0.116.0", "uvicorn==0.31.0"]
