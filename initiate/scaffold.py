from __future__ import annotations

from pathlib import Path


def init_project(
    target_dir: Path,
    project_type: str = "script",
    entry_file: str = "app.py",
    force: bool = False,
) -> list[str]:
    target = target_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    entry_path = target / entry_file
    readme_path = target / "README.md"
    pyproject_path = target / "pyproject.toml"

    created: list[str] = []

    if not force:
        for path in (entry_path, readme_path, pyproject_path):
            if path.exists():
                raise RuntimeError(
                    f"Refusing to overwrite existing file: {path}. "
                    "Use --force to overwrite starter files."
                )

    entry_source = _entry_template(project_type)
    readme_source = _starter_readme(project_type, entry_file)
    pyproject_source = _starter_pyproject(target.name or "initiate_app")

    entry_path.write_text(entry_source, encoding="utf-8")
    readme_path.write_text(readme_source, encoding="utf-8")
    pyproject_path.write_text(pyproject_source, encoding="utf-8")

    created.extend([str(entry_path), str(readme_path), str(pyproject_path)])
    return created


def _entry_template(project_type: str) -> str:
    if project_type == "fastapi":
        return (
            "import initiate\n"
            "from fastapi import FastAPI\n\n"
            "initiate.run()\n\n"
            "app = FastAPI()\n\n"
            "@app.get('/')\n"
            "def health() -> dict[str, str]:\n"
            "    return {'status': 'ok'}\n"
        )
    if project_type == "flask":
        return (
            "import initiate\n"
            "from flask import Flask\n\n"
            "initiate.run()\n\n"
            "app = Flask(__name__)\n\n"
            "@app.get('/')\n"
            "def health() -> dict[str, str]:\n"
            "    return {'status': 'ok'}\n"
        )
    if project_type == "streamlit":
        return (
            "import initiate\n"
            "import streamlit as st\n\n"
            "initiate.run()\n\n"
            "st.title('Initiate Streamlit Starter')\n"
            "st.write('Your app is running with automatic dependency setup.')\n"
        )
    return (
        "import initiate\n\n"
        "initiate.run()\n\n"
        "print('Hello from Initiate starter app')\n"
    )


def _starter_readme(project_type: str, entry_file: str) -> str:
    return (
        "# Starter Project\n\n"
        f"Template type: `{project_type}`\n\n"
        "## Run\n\n"
        "```bash\n"
        f"python {entry_file}\n"
        "```\n\n"
        "This project uses `initiate.run()` to:\n\n"
        "- create a managed virtual environment\n"
        "- install dependencies from imports\n"
        "- run the application without manual venv activation\n"
    )


def _starter_pyproject(project_name: str) -> str:
    safe_name = project_name.replace(" ", "_").replace("-", "_")
    return (
        "[project]\n"
        f"name = \"{safe_name}\"\n"
        "version = \"0.1.0\"\n"
        "requires-python = \">=3.9\"\n"
        "dependencies = []\n"
    )
