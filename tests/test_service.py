from __future__ import annotations

from pathlib import Path
import plistlib
import subprocess
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET

from runtime.models import Profile
from runtime.service import ServiceManager


class ServiceManagerTests(unittest.TestCase):
    def test_windows_task_definition_uses_fixed_service_argv(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            profile = _profile(root)
            manager = ServiceManager(
                profile,
                platform_name="windows",
                python_executable="C:/Python/python.exe",
                cli_path=root / "scripts" / "lark_sync.py",
            )

            document = ET.fromstring(manager.windows_task_xml())
            namespace = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
            self.assertEqual(document.findtext(".//task:Command", namespaces=namespace), "C:/Python/python.exe")
            arguments = document.findtext(".//task:Arguments", namespaces=namespace) or ""
            self.assertIn('"' + str(profile.source_path) + '"', arguments)
            self.assertTrue(arguments.endswith('"service-run"'))
            self.assertEqual(manager.service_identity, "LarkAutoSync-demo")

    def test_macos_launch_agent_is_profile_scoped_and_uses_argument_array(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            profile = _profile(root)
            manager = ServiceManager(
                profile,
                platform_name="darwin",
                home_directory=root / "home",
                python_executable="/usr/bin/python3",
                cli_path=root / "scripts" / "lark_sync.py",
            )

            plist = plistlib.loads(manager.macos_plist_bytes())
            self.assertEqual(plist["Label"], "com.codex.lark-auto-sync.demo")
            self.assertEqual(plist["ProgramArguments"], [
                "/usr/bin/python3",
                str(root / "scripts" / "lark_sync.py"),
                "--profile",
                str(profile.source_path),
                "service-run",
            ])
            self.assertTrue(manager.definition_path.name.endswith(".plist"))
            self.assertIn("Library", str(manager.definition_path))

    def test_windows_install_and_uninstall_target_only_profile_identity(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            calls: list[list[str]] = []

            def runner(arguments, **_kwargs):
                calls.append(list(arguments))
                return subprocess.CompletedProcess(arguments, 0, "", "")

            manager = ServiceManager(
                _profile(root),
                platform_name="windows",
                runner=runner,
                cli_path=root / "scripts" / "lark_sync.py",
            )
            manager.install()
            manager.uninstall()

            self.assertEqual(calls[0][0:4], ["schtasks.exe", "/Create", "/TN", "LarkAutoSync-demo"])
            self.assertEqual(calls[1], ["schtasks.exe", "/Delete", "/TN", "LarkAutoSync-demo", "/F"])

    def test_windows_status_requires_running_task_state(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            responses = iter([
                subprocess.CompletedProcess([], 0, '"HOST","\\LarkAutoSync-demo","N/A","Running"\n', ""),
                subprocess.CompletedProcess([], 0, '"HOST","\\LarkAutoSync-demo","N/A","Ready"\n', ""),
            ])

            def runner(arguments, **_kwargs):
                return next(responses)

            manager = ServiceManager(_profile(root), platform_name="windows", runner=runner)
            self.assertTrue(manager.status())
            self.assertFalse(manager.status())

    def test_windows_run_starts_registered_task(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            calls: list[list[str]] = []

            def runner(arguments, **_kwargs):
                calls.append(list(arguments))
                return subprocess.CompletedProcess(arguments, 0, "", "")

            manager = ServiceManager(_profile(root), platform_name="windows", runner=runner)
            manager.run()

            self.assertEqual(calls, [["schtasks.exe", "/Run", "/TN", "LarkAutoSync-demo"]])


def _profile(root: Path) -> Profile:
    workspace = root / "workspace"
    workspace.mkdir()
    return Profile(
        id="demo",
        source_path=root / "profile.yaml",
        workspace_root=workspace,
        data={"workspace": {"state_directory": ".state"}},
    )


if __name__ == "__main__":
    unittest.main()
