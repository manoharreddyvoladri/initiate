# Initiate

Build once, run anywhere (with less setup pain).

`initiate` is a production-focused Python runtime bootstrapper for developers and teams that want faster onboarding, fewer environment bugs, and reproducible execution.

## Why This Project

Most Python failures in real teams are not business logic bugs. They are runtime friction:

- `ModuleNotFoundError`
- wrong Python version
- unactivated virtual environments
- different package versions across laptops, CI, and servers

`initiate` automates those steps so developers can ship faster.

## What It Does

- Detects dependencies from imports and requirements files
- Creates isolated virtual environments automatically
- Installs dependencies with configurable enterprise network settings
- Verifies install health with `pip check`
- Auto-heals common startup import failures with retries
- Supports reproducible installs via `initiate.lock`
- Enforces trust/block security policies
- Provides CLI tooling for `run`, `lock`, `doctor`, `clean`

## Install

```bash
pip install initiate
```

## Quick Start

Add this to your app entry file:

```python
import initiate

initiate.run()
```

Run as usual:

```bash
python app.py
```

No manual virtualenv activation is needed.

## CLI

### Run

```bash
initiate run app.py
```

Backward-compatible:

```bash
python -m initiate app.py
```

### Create Lockfile

```bash
initiate lock app.py
```

### Strict Reproducible Mode

```bash
initiate run app.py --strict-lock
```

Strict mode installs only from `initiate.lock`.

### Diagnostics

```bash
initiate doctor app.py
initiate doctor app.py --json
```

### Clean Cache

```bash
initiate clean
initiate clean --all
```

## Test It On Your Laptop

Use these exact steps from a terminal in this repository:

1. Install package + test deps:
```bash
pip install -e ".[dev]"
```
2. Run automated tests:
```bash
pytest
```
3. Create a sample app file:
```python
# save as demo_app.py
import initiate
import json

initiate.run()
print(json.dumps({"status": "ok"}))
```
4. Generate lockfile:
```bash
python -m initiate lock demo_app.py
```
5. Run with strict reproducibility:
```bash
python -m initiate run demo_app.py --strict-lock
```
6. Run diagnostics:
```bash
python -m initiate doctor demo_app.py --json
```

If all steps pass, your laptop setup is healthy.

## Runtime Configuration

Configure in `pyproject.toml`:

```toml
[tool.initiate]
retries = 3
update = true
use-lock = true
strict-lock = false
auto-lock = false

trusted-packages = ["fastapi", "uvicorn", "pydantic"]
blocked-packages = ["evilpkg"]
enforce-trusted = false

index-url = "https://pypi.org/simple"
extra-index-urls = []
trusted-hosts = []
proxy = ""
cert = ""
timeout = 30
network-retries = 4
offline = false
no-cache = false
```

You can also use `initiate.toml` and env vars like `INITIATE_STRICT_LOCK`, `INITIATE_INDEX_URL`, and `INITIATE_PROXY`.

## Framework Support

Auto-start support:

- FastAPI
- Flask
- Streamlit
- Django (when `manage.py` exists)

Detected but not auto-launched:

- Gradio
- Dash

## Reliability and Security

- Concurrent install lock at `.initiate/.install.lock`
- `pip check` validation after installs
- Package blocklist and trusted-allowlist policies
- Strict lock mode to prevent dependency drift

## CI

GitHub Actions matrix tests are included in:

- `.github/workflows/ci.yml`

## Publish

### Push to GitHub

```bash
git init
git add .
git commit -m "feat: production-grade initiate runtime"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

### Publish to PyPI

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
python -m twine upload dist/*
```

## Remaining Limitations

1. Dynamic imports (`importlib`/plugin strings) are not always detectable statically.
2. Import mismatch auto-heal remains heuristic for complex dependency graphs.
3. OS/native system dependencies are out of pip scope.
4. Universal framework lifecycle orchestration still requires adapters for every ecosystem.
