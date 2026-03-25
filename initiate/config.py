from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None


@dataclass(frozen=True)
class PipNetworkConfig:
    index_url: str | None = None
    extra_index_urls: tuple[str, ...] = ()
    trusted_hosts: tuple[str, ...] = ()
    proxy: str | None = None
    cert: str | None = None
    timeout: int | None = None
    retries: int | None = None
    offline: bool = False
    no_cache: bool = False


@dataclass(frozen=True)
class SecurityConfig:
    trusted_packages: tuple[str, ...] = ()
    blocked_packages: tuple[str, ...] = ()
    enforce_trusted: bool = False


@dataclass(frozen=True)
class RuntimeConfig:
    retries: int = 3
    update: bool = True
    use_lock: bool = True
    strict_lock: bool = False
    auto_lock: bool = False
    network: PipNetworkConfig = field(default_factory=PipNetworkConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)


def load_runtime_config(project_root: Path) -> RuntimeConfig:
    pyproject = _load_toml(project_root / "pyproject.toml")
    initiate_toml = _load_toml(project_root / "initiate.toml")

    py_tool = pyproject.get("tool", {}).get("initiate", {})
    ini_tool = initiate_toml.get("initiate", {}) if "initiate" in initiate_toml else initiate_toml

    merged = _merge_dicts(py_tool, ini_tool)
    merged = _merge_env_overrides(merged)

    trusted_packages = tuple(_normalize_package_list(merged.get("trusted-packages", [])))
    blocked_packages = tuple(_normalize_package_list(merged.get("blocked-packages", [])))
    enforce_trusted = _as_bool(merged.get("enforce-trusted"), default=bool(trusted_packages))

    network = PipNetworkConfig(
        index_url=_as_str(merged.get("index-url")),
        extra_index_urls=tuple(_as_list(merged.get("extra-index-urls"))),
        trusted_hosts=tuple(_as_list(merged.get("trusted-hosts"))),
        proxy=_as_str(merged.get("proxy")),
        cert=_as_str(merged.get("cert")),
        timeout=_as_int(merged.get("timeout")),
        retries=_as_int(merged.get("network-retries")),
        offline=_as_bool(merged.get("offline"), default=False),
        no_cache=_as_bool(merged.get("no-cache"), default=False),
    )

    return RuntimeConfig(
        retries=_as_int(merged.get("retries"), default=3) or 3,
        update=_as_bool(merged.get("update"), default=True),
        use_lock=_as_bool(merged.get("use-lock"), default=True),
        strict_lock=_as_bool(merged.get("strict-lock"), default=False),
        auto_lock=_as_bool(merged.get("auto-lock"), default=False),
        network=network,
        security=SecurityConfig(
            trusted_packages=trusted_packages,
            blocked_packages=blocked_packages,
            enforce_trusted=enforce_trusted,
        ),
    )


def _load_toml(path: Path) -> dict[str, object]:
    if not tomllib or not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _merge_dicts(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in override.items():
        merged[key] = value
    return merged


def _merge_env_overrides(config: dict[str, object]) -> dict[str, object]:
    merged = dict(config)
    env_map = {
        "INITIATE_RETRIES": "retries",
        "INITIATE_UPDATE": "update",
        "INITIATE_USE_LOCK": "use-lock",
        "INITIATE_STRICT_LOCK": "strict-lock",
        "INITIATE_AUTO_LOCK": "auto-lock",
        "INITIATE_INDEX_URL": "index-url",
        "INITIATE_EXTRA_INDEX_URLS": "extra-index-urls",
        "INITIATE_TRUSTED_HOSTS": "trusted-hosts",
        "INITIATE_PROXY": "proxy",
        "INITIATE_CERT": "cert",
        "INITIATE_TIMEOUT": "timeout",
        "INITIATE_NETWORK_RETRIES": "network-retries",
        "INITIATE_OFFLINE": "offline",
        "INITIATE_NO_CACHE": "no-cache",
        "INITIATE_TRUSTED_PACKAGES": "trusted-packages",
        "INITIATE_BLOCKED_PACKAGES": "blocked-packages",
        "INITIATE_ENFORCE_TRUSTED": "enforce-trusted",
    }
    for env_key, config_key in env_map.items():
        if env_key not in os.environ:
            continue
        value = os.environ[env_key]
        if config_key in {"extra-index-urls", "trusted-hosts", "trusted-packages", "blocked-packages"}:
            merged[config_key] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            merged[config_key] = value
    return merged


def _normalize_package_list(value: object) -> list[str]:
    return [item.lower() for item in _as_list(value)]


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: object, default: int | None = None) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return None
