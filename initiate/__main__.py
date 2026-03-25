from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .runtime import clean, create_lock, doctor, run
from .scaffold import init_project


def main() -> None:
    parser = argparse.ArgumentParser(description="Initiate runtime bootstrapper")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a script with managed runtime")
    _add_run_arguments(run_parser)

    lock_parser = subparsers.add_parser("lock", help="Create or refresh initiate.lock")
    lock_parser.add_argument("script", type=Path, help="Python entry script")
    lock_parser.add_argument("--python", dest="python_version", help="Target Python version (ex: 3.12)")
    lock_parser.add_argument("--no-update", action="store_true", help="Do not pass --upgrade to pip")

    doctor_parser = subparsers.add_parser("doctor", help="Print environment diagnostics")
    doctor_parser.add_argument("script", nargs="?", type=Path, help="Optional entry script for dependency scan")
    doctor_parser.add_argument("--json", action="store_true", help="Print diagnostics as JSON")
    doctor_parser.add_argument("--fix", action="store_true", help="Try automatic repair for common environment issues")

    clean_parser = subparsers.add_parser("clean", help="Remove cached runtime artifacts")
    clean_parser.add_argument("--all", action="store_true", help="Also remove initiate.lock")

    init_parser = subparsers.add_parser("init", help="Create a starter project")
    init_parser.add_argument("path", nargs="?", default=Path("."), type=Path, help="Target project directory")
    init_parser.add_argument(
        "--type",
        dest="project_type",
        default="script",
        choices=["script", "fastapi", "flask", "streamlit"],
        help="Starter template type",
    )
    init_parser.add_argument("--entry", default="app.py", help="Entry file name")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing starter files")

    # Backward-compatible mode: python -m initiate app.py ...
    argv = sys.argv[1:]
    known_commands = {"run", "lock", "doctor", "clean", "init", "-h", "--help"}
    if argv and argv[0] not in known_commands:
        argv = ["run", *argv]
    elif not argv:
        parser.print_help()
        return

    args, unknown = parser.parse_known_args(argv)
    if args.command != "run" and unknown:
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")

    if args.command == "run":
        script_args = list(unknown)
        if script_args and script_args[0] == "--":
            script_args = script_args[1:]
        run(
            script=args.script,
            auto_start=not args.no_auto_start,
            python_version=args.python_version,
            host=args.host,
            port=args.port,
            reload=not args.no_reload,
            retries=args.retries,
            update=not args.no_update,
            use_lock=not args.no_lock,
            strict_lock=args.strict_lock,
            auto_lock=args.auto_lock,
            script_args=script_args,
        )
        return

    if args.command == "lock":
        path = create_lock(
            script=args.script,
            python_version=args.python_version,
            update=not args.no_update,
        )
        print(path)
        return

    if args.command == "doctor":
        data = doctor(script=args.script, fix=args.fix)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            _print_doctor(data)
        return

    if args.command == "clean":
        clean(remove_lock=args.all)
        return

    if args.command == "init":
        created = init_project(
            target_dir=args.path,
            project_type=args.project_type,
            entry_file=args.entry,
            force=args.force,
        )
        for item in created:
            print(item)
        return


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("script", type=Path, help="Python script to run")
    parser.add_argument("--python", dest="python_version", help="Target Python version (ex: 3.12)")
    parser.add_argument("--host", default="127.0.0.1", help="Server host for auto-start frameworks")
    parser.add_argument("--port", default=8000, type=int, help="Server port for auto-start frameworks")
    parser.add_argument("--retries", default=3, type=int, help="Retries for auto-healing missing modules")
    parser.add_argument("--no-reload", action="store_true", help="Disable FastAPI/Flask auto-reload")
    parser.add_argument("--no-auto-start", action="store_true", help="Disable framework auto-start")
    parser.add_argument("--no-update", action="store_true", help="Install exact dependency specs without --upgrade")
    parser.add_argument("--no-lock", action="store_true", help="Ignore initiate.lock even if present")
    parser.add_argument("--strict-lock", action="store_true", help="Install only from initiate.lock")
    parser.add_argument("--auto-lock", action="store_true", help="Refresh initiate.lock after successful install")


def _print_doctor(data: dict[str, object]) -> None:
    for key in sorted(data.keys()):
        print(f"{key}: {data[key]}")


if __name__ == "__main__":
    main()
