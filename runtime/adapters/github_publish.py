"""Profile-bound GitHub publication with isolated worktrees and exact staging."""

from __future__ import annotations

import re
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterable
from urllib.parse import unquote, urlparse

from runtime.adapters.local_publish import PublishResult, _sha256
from runtime.models import Profile
from runtime.state import create_state_paths, redact_error


class GitHubPublishError(ValueError):
    """A Profile or file list cannot be safely published to GitHub."""


_GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def publish_github(profile: Profile, files: list[Path], message: str) -> PublishResult:
    """Publish only Profile-allowlisted output files using a fresh worktree.

    Files must live under ``publish.local.directory``. Their relative paths
    are copied below ``publish.github.path`` and are the *only* paths passed to
    ``git add --``. A changed remote tip is reported as retryable instead of
    attempting to overwrite it.
    """
    github = _github_settings(profile)
    source_root = _profile_path(profile, _local_directory(profile), "publish.local.directory")
    allowed_files = _allowlisted_files(files, source_root)
    destination_root = _repository_relative_path(github["path"])
    commit_message = _commit_message(message)
    remote = _repository_remote(github["repository"])
    branch = _validated_branch(github["branch"])

    state_paths = create_state_paths(profile)
    worktrees_root = state_paths.root / "publish-worktrees"
    worktrees_root.mkdir(parents=True, exist_ok=True)
    worktree = Path(tempfile.mkdtemp(prefix="publish-", dir=worktrees_root))

    try:
        _git(["clone", "--no-checkout", remote, str(worktree)])
        _git(["-C", str(worktree), "fetch", "origin", branch])
        remote_tip = _git_output(["-C", str(worktree), "rev-parse", f"refs/remotes/origin/{branch}"])
        # This is a fresh clone, so switching to its configured local branch
        # cannot discard local work. Keeping its name identical lets the
        # normal, non-force `git push origin <branch>` form be exact.
        _git(["-C", str(worktree), "switch", branch])
        _verify_origin(worktree, remote)

        staged_paths: list[str] = []
        digests: list[str] = []
        for source, relative_path in allowed_files:
            destination = worktree / destination_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if _sha256(destination) != _sha256(source):
                raise GitHubPublishError("copied_digest_mismatch")
            staged_paths.append((destination_root / relative_path).as_posix())
            digests.append(_sha256(source))

        _git(["-C", str(worktree), "add", "--", *staged_paths])
        actual_staged = _staged_paths(worktree)
        if actual_staged != tuple(staged_paths):
            raise GitHubPublishError("staged_paths_do_not_match_allowlist")

        if not actual_staged:
            return PublishResult(
                ok=True,
                status="no_changes",
                destination=(destination_root.as_posix() or "."),
                files=tuple(staged_paths),
            )

        _git(
            [
                "-C",
                str(worktree),
                "-c",
                "user.name=lark-auto-sync",
                "-c",
                "user.email=lark-auto-sync@localhost",
                "commit",
                "-m",
                commit_message,
            ]
        )
        commit = _git_output(["-C", str(worktree), "rev-parse", "HEAD"])

        if _remote_tip(worktree, branch) != remote_tip:
            return _retryable_remote_change(staged_paths)
        pushed = _run_git(["-C", str(worktree), "push", "origin", branch], check=False)
        if pushed.returncode != 0:
            if _remote_tip(worktree, branch) != remote_tip:
                return _retryable_remote_change(staged_paths)
            raise GitHubPublishError(_git_error("git_push_failed", pushed))
        if _remote_tip(worktree, branch) != commit:
            raise GitHubPublishError("remote_verification_failed")

        return PublishResult(
            ok=True,
            status="published",
            sha256=digests[0] if len(digests) == 1 else None,
            destination=destination_root.as_posix() or ".",
            commit=commit,
            files=tuple(staged_paths),
        )
    except GitHubPublishError:
        raise
    except OSError as error:
        raise GitHubPublishError("github_publish_failed") from error
    finally:
        # This directory was created by mkdtemp under this profile's state root.
        shutil.rmtree(worktree, ignore_errors=True)


def _github_settings(profile: Profile) -> dict[str, str]:
    publish = profile.data.get("publish")
    github = publish.get("github") if isinstance(publish, dict) else None
    if not isinstance(github, dict):
        raise GitHubPublishError("github_publish_not_configured")
    required = ("repository", "branch", "path")
    if any(not isinstance(github.get(key), str) or not github[key].strip() for key in required):
        raise GitHubPublishError("invalid_github_publish_configuration")
    return {key: github[key].strip() for key in required}


def _local_directory(profile: Profile) -> str:
    publish = profile.data.get("publish")
    local = publish.get("local") if isinstance(publish, dict) else None
    directory = local.get("directory") if isinstance(local, dict) else None
    if not isinstance(directory, str) or not directory.strip():
        raise GitHubPublishError("local_publish_not_configured")
    return directory


def _profile_path(profile: Profile, value: str, field: str) -> Path:
    try:
        return profile.resolve_path(value)
    except (OSError, TypeError, ValueError) as error:
        raise GitHubPublishError(f"path_escapes_workspace:{field}") from error


def _allowlisted_files(files: Iterable[Path], source_root: Path) -> list[tuple[Path, Path]]:
    if not isinstance(files, list) or not files:
        raise GitHubPublishError("files_required")
    root = source_root.resolve()
    allowed: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for candidate in files:
        path = Path(candidate)
        if path.is_symlink() or not path.is_file():
            raise GitHubPublishError("file_must_be_regular")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as error:
            raise GitHubPublishError("file_outside_allowlisted_directory") from error
        if relative in seen:
            raise GitHubPublishError("duplicate_publish_file")
        seen.add(relative)
        allowed.append((resolved, relative))
    return allowed


def _repository_relative_path(value: str) -> Path:
    candidate = Path(value.replace("\\", "/"))
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise GitHubPublishError("unsafe_github_path")
    return Path(*candidate.parts)


def _repository_remote(repository: str) -> str:
    if _GITHUB_REPOSITORY.fullmatch(repository):
        return f"https://github.com/{repository}.git"
    if repository.startswith("file://"):
        parsed = urlparse(repository)
        local_path = Path(unquote(parsed.path))
    else:
        local_path = Path(repository)
    if local_path.is_absolute() and local_path.is_dir():
        return str(local_path.resolve())
    raise GitHubPublishError("unsafe_github_repository")


def _validated_branch(branch: str) -> str:
    # Check this before passing the Profile value to fetch, push, or ls-remote.
    # Git owns the ref grammar; arguments are still passed without a shell.
    completed = _run_git(["check-ref-format", "--branch", branch], check=False)
    if completed.returncode != 0:
        raise GitHubPublishError("unsafe_github_branch")
    return branch


def _commit_message(value: str) -> str:
    if not isinstance(value, str):
        raise GitHubPublishError("commit_message_required")
    message = value.strip()
    if not message or "\x00" in message or len(message) > 200:
        raise GitHubPublishError("invalid_commit_message")
    return message


def _verify_origin(worktree: Path, expected_remote: str) -> None:
    actual_remote = _git_output(["-C", str(worktree), "remote", "get-url", "origin"])
    if actual_remote != expected_remote:
        raise GitHubPublishError("repository_origin_mismatch")


def _staged_paths(worktree: Path) -> tuple[str, ...]:
    completed = _run_git(["-C", str(worktree), "diff", "--cached", "--name-only", "-z"])
    return tuple(path for path in completed.stdout.split("\0") if path)


def _remote_tip(worktree: Path, branch: str) -> str | None:
    completed = _run_git(["-C", str(worktree), "ls-remote", "--heads", "origin", branch])
    output = completed.stdout.strip()
    return output.split()[0] if output else None


def _retryable_remote_change(staged_paths: list[str]) -> PublishResult:
    return PublishResult(
        ok=False,
        status="remote_changed",
        files=tuple(staged_paths),
        error="remote_changed",
        retryable=True,
    )


def _git(arguments: list[str]) -> None:
    completed = _run_git(arguments, check=False)
    if completed.returncode != 0:
        raise GitHubPublishError(_git_error("git_command_failed", completed))


def _git_output(arguments: list[str]) -> str:
    completed = _run_git(arguments)
    return completed.stdout.strip()


def _run_git(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        raise GitHubPublishError(_git_error("git_command_failed", completed))
    return completed


def _git_error(prefix: str, completed: subprocess.CompletedProcess[str]) -> str:
    detail = redact_error(completed.stderr or completed.stdout).strip()
    return f"{prefix}:{detail}" if detail else prefix
