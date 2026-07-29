from __future__ import annotations

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runtime.adapters.github_publish import GitHubPublishError, publish_github
from runtime.adapters.local_publish import publish_local
from runtime.models import Profile


class PublisherTests(unittest.TestCase):
    def test_local_publish_copies_atomically_and_returns_digest(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            source = root / "source.md"
            destination = root / "nested" / "note.md"
            source.write_text("# Note\n", encoding="utf-8")

            result = publish_local(source, destination)

            self.assertTrue(result.ok)
            self.assertEqual(destination.read_text(encoding="utf-8"), "# Note\n")
            self.assertEqual(len(result.sha256 or ""), 64)
            self.assertFalse(list(destination.parent.glob("*.tmp")))

    def test_github_publish_uses_local_bare_repo_and_stages_only_allowlisted_output(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            remote = _seed_bare_repo(root)

            profile, output = _profile(root, remote)
            note = output / "meeting.md"
            note.write_text("# Meeting\n", encoding="utf-8")
            outside = root / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")

            result = publish_github(profile, [note], "publish meeting")

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "published")
            self.assertEqual(result.files, ("meetings/meeting.md",))
            with self.assertRaises(GitHubPublishError):
                publish_github(profile, [outside], "must fail")

            verified = root / "verified"
            _git(["clone", str(remote), str(verified)])
            self.assertEqual((verified / "meetings" / "meeting.md").read_text(encoding="utf-8"), "# Meeting\n")
            changed = _git_output(["-C", str(verified), "show", "--pretty=", "--name-only", "HEAD"]).splitlines()
            self.assertEqual(changed, ["meetings/meeting.md"])

    def test_github_publish_reports_remote_changes_without_pushing(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            remote = _seed_bare_repo(root)
            profile, output = _profile(root, remote)
            note = output / "meeting.md"
            note.write_text("# Meeting\n", encoding="utf-8")

            with patch("runtime.adapters.github_publish._remote_tip", return_value="new-remote-tip"):
                result = publish_github(profile, [note], "publish meeting")

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "remote_changed")
            self.assertTrue(result.retryable)
            verified = root / "verified"
            _git(["clone", str(remote), str(verified)])
            self.assertFalse((verified / "meetings" / "meeting.md").exists())


def _profile(root: Path, remote: Path) -> tuple[Profile, Path]:
    workspace = root / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    profile = Profile(
        id="demo",
        source_path=root / "profile.yaml",
        workspace_root=workspace,
        data={
            "workspace": {"state_directory": ".state"},
            "publish": {
                "local": {"directory": "output"},
                "github": {"repository": str(remote), "branch": "main", "path": "meetings"},
            },
        },
    )
    return profile, output


def _seed_bare_repo(root: Path) -> Path:
    remote = root / "remote.git"
    _git(["init", "--bare", "--initial-branch=main", str(remote)])
    seed = root / "seed"
    _git(["clone", str(remote), str(seed)])
    _git(["-C", str(seed), "config", "user.name", "Test User"])
    _git(["-C", str(seed), "config", "user.email", "test@example.com"])
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git(["-C", str(seed), "add", "--", "README.md"])
    _git(["-C", str(seed), "commit", "-m", "seed"])
    _git(["-C", str(seed), "push", "origin", "main"])
    return remote


def _git(arguments: list[str]) -> None:
    completed = subprocess.run(["git", *arguments], text=True, capture_output=True, check=False)
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)


def _git_output(arguments: list[str]) -> str:
    completed = subprocess.run(["git", *arguments], text=True, capture_output=True, check=False)
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout


if __name__ == "__main__":
    unittest.main()
