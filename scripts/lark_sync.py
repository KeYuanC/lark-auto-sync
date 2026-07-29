from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
import zipfile
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lark-sync")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--profile", type=Path)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor")
    commands.add_parser("init")
    commands.add_parser("start")
    commands.add_parser("stop")
    commands.add_parser("status")
    commands.add_parser("logs")
    commands.add_parser("heartbeat-prompt")
    commands.add_parser("service-run")
    package = commands.add_parser("package")
    package.add_argument("--output", type=Path, required=True)
    queue = commands.add_parser("queue")
    queue_commands = queue.add_subparsers(dest="queue_command", required=True)
    queue_commands.add_parser("list")
    finalize = queue_commands.add_parser("finalize")
    finalize.add_argument("--job-id", required=True)
    finalize.add_argument("--extracted-json", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    if args.version:
        return _emit(args, _envelope(True, "version", None, "ready", details={"version": "0.1.0"}))
    if not args.command:
        return _emit(args, _envelope(False, "help", None, "invalid_request", ["command_required"]))
    if args.command == "package":
        return _emit(args, _package(None, args.output))
    if args.profile is None:
        return _emit(args, _envelope(False, args.command, None, "invalid_request", ["profile_required"]))

    profile, failure = _load_profile(args.profile, args.command)
    if failure is not None:
        return _emit(args, failure)
    assert profile is not None

    if args.command == "doctor":
        return _emit(args, _doctor(profile))
    if args.command == "init":
        return _emit(args, _envelope(True, "init", profile.id, "ready", details={"profile_path": str(profile.source_path)}))
    if args.command == "heartbeat-prompt":
        from runtime.extraction import heartbeat_prompt
        return _emit(args, _envelope(True, "heartbeat-prompt", profile.id, "ok", details={"prompt": heartbeat_prompt(profile.source_path)}))
    if args.command == "logs":
        from runtime.state import create_state_paths
        return _emit(args, _envelope(True, "logs", profile.id, "ok", details={"path": str(create_state_paths(profile).logs)}))
    if args.command in {"start", "stop", "status"}:
        return _emit(args, _service_command(profile, args.command))
    if args.command == "service-run":
        return _emit(args, _service_run(profile))
    if args.command == "queue" and args.queue_command == "list":
        return _emit(args, _queue_list(profile))
    if args.command == "queue" and args.queue_command == "finalize":
        return _emit(args, _queue_finalize(profile, args.job_id, args.extracted_json))
    return _emit(args, _envelope(False, args.command, profile.id, "invalid_request", ["unknown_command"]))


def _load_profile(path: Path, operation: str) -> tuple[Any | None, dict[str, Any] | None]:
    try:
        from runtime.config import ProfileError, load_profile
    except ImportError:
        return None, _envelope(False, operation, None, "dependency_unavailable", ["profile_runtime_unavailable"])
    try:
        return load_profile(path), None
    except (OSError, ProfileError, ValueError):
        return None, _envelope(False, operation, None, "profile_invalid", ["profile_validation_failed"])


def _doctor(profile: Any) -> dict[str, Any]:
    try:
        from runtime.state import create_state_paths
        create_state_paths(profile)
    except (ImportError, OSError, ValueError):
        return _envelope(False, "doctor", profile.id, "failed", ["state_initialization_failed"])
    dependencies = {
        "lark_cli": bool(shutil.which("lark-cli") or shutil.which("lark-cli.cmd")),
        "git": bool(shutil.which("git")),
        "gh": bool(shutil.which("gh")),
    }
    return _envelope(True, "doctor", profile.id, "ok", details={"profile_valid": True, "dependencies": dependencies})


def _queue_list(profile: Any) -> dict[str, Any]:
    try:
        from runtime.queue import Queue
        jobs = Queue(profile).list_pending()
    except ImportError:
        return _envelope(False, "queue.list", profile.id, "dependency_unavailable", ["queue_runtime_unavailable"])
    except (OSError, ValueError):
        return _envelope(False, "queue.list", profile.id, "failed", ["queue_unavailable"])
    return _envelope(True, "queue.list", profile.id, "ok", details={"jobs": jobs})


def _queue_finalize(profile: Any, job_id: str, extracted_json: Path) -> dict[str, Any]:
    try:
        from runtime.finalize import finalize_job
    except ImportError:
        return _envelope(False, "queue.finalize", profile.id, "dependency_unavailable", ["finalizer_unavailable"])
    result = finalize_job(profile, job_id, extracted_json)
    reason = result.get("reason") or result.get("error") or "finalize_failed"
    return _envelope(bool(result.get("ok")), "queue.finalize", profile.id, str(result.get("status") or "failed"), [] if result.get("ok") else [str(reason)], details=result)


def _service_command(profile: Any, command: str) -> dict[str, Any]:
    try:
        from runtime.service import ServiceManager
        manager = ServiceManager(profile)
        if command == "start":
            path = manager.install()
            return _envelope(True, command, profile.id, "started", details={"definition": str(path)})
        if command == "stop":
            manager.uninstall()
            return _envelope(True, command, profile.id, "stopped")
        return _envelope(True, command, profile.id, "running" if manager.status() else "stopped")
    except (OSError, ValueError):
        return _envelope(False, command, profile.id, "failed", ["service_operation_failed"])


def _service_run(profile: Any) -> dict[str, Any]:
    from runtime.collector import Collector
    from runtime.lark_client import LarkClient
    collector = Collector(profile, LarkClient())
    interval = int(profile.data.get("source", {}).get("poll_seconds", 30))
    interval = min(max(interval, 5), 300)
    try:
        while True:
            collector.scan_once()
            time.sleep(interval)
    except KeyboardInterrupt:
        return _envelope(True, "service-run", profile.id, "stopped")


def _package(profile: Any | None, output: Path) -> dict[str, Any]:
    del profile
    allowed = ("SKILL.md", "agents", "scripts", "runtime", "schemas", "profiles", "references", "tests", "requirements.txt")
    excluded = {".git", "__pycache__", ".state", ".automation-state", "dist"}
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in allowed:
            path = SKILL_ROOT / item
            if path.is_file():
                archive.write(path, f"lark-auto-sync/{path.name}")
                continue
            for candidate in path.rglob("*"):
                relative = candidate.relative_to(SKILL_ROOT)
                lowered = "/".join(relative.parts).lower()
                if not candidate.is_file() or any(part in excluded for part in relative.parts) or any(token in lowered for token in ("secret", "credential", ".env", "token")):
                    continue
                archive.write(candidate, f"lark-auto-sync/{relative.as_posix()}")
    return _envelope(True, "package", None, "created", details={"path": str(output)})


def _envelope(
    ok: bool,
    operation: str,
    profile: str | None,
    status: str,
    errors: list[str] | None = None,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": ok, "operation": operation, "profile": profile, "status": status, "errors": errors or []}
    if details is not None:
        payload["details"] = details
    return payload


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{payload['operation']}: {payload['status']}")
        for error in payload["errors"]:
            print(f"- {error}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
