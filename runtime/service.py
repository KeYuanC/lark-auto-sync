"""Create and operate one low-privilege collector service per Profile."""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys
from typing import Callable
import xml.etree.ElementTree as ET

from runtime.models import Profile
from runtime.safety import require_safe_identifier
from runtime.state import create_state_paths


class ServiceDefinitionError(ValueError):
    """A service definition or lifecycle operation is unsafe."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


class ServiceManager:
    def __init__(
        self,
        profile: Profile,
        *,
        python_executable: str | Path | None = None,
        cli_path: str | Path | None = None,
        home_directory: str | Path | None = None,
        platform_name: str | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.profile = profile
        self._profile_id = require_safe_identifier(profile.id)
        self.platform_name = (platform_name or sys.platform).lower()
        # Preserve an explicitly supplied platform-specific executable string.
        # Resolving `/usr/bin/python3` on Windows would corrupt a macOS definition.
        self.python_executable = str(python_executable or sys.executable)
        self.cli_path = Path(cli_path or Path(__file__).resolve().parents[1] / "scripts" / "lark_sync.py").resolve()
        self.home_directory = Path(home_directory or Path.home()).resolve()
        self._runner = runner or subprocess.run

    @property
    def service_identity(self) -> str:
        if self.platform_name.startswith("win"):
            return f"LarkAutoSync-{self._profile_id}"
        if self.platform_name in {"darwin", "macos"}:
            return f"com.codex.lark-auto-sync.{self._profile_id}"
        raise ServiceDefinitionError("unsupported_platform")

    @property
    def definition_path(self) -> Path:
        if self.platform_name.startswith("win"):
            return create_state_paths(self.profile).root / "services" / f"{self.service_identity}.xml"
        if self.platform_name in {"darwin", "macos"}:
            return self.home_directory / "Library" / "LaunchAgents" / f"{self.service_identity}.plist"
        raise ServiceDefinitionError("unsupported_platform")

    def command_argv(self) -> list[str]:
        return [self.python_executable, str(self.cli_path), "--profile", str(self.profile.source_path.resolve()), "service-run"]

    def windows_task_xml(self) -> bytes:
        if not self.platform_name.startswith("win"):
            raise ServiceDefinitionError("windows_definition_requested_on_non_windows")
        namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
        ET.register_namespace("", namespace)
        task = ET.Element(f"{{{namespace}}}Task", {"version": "1.4"})
        principals = ET.SubElement(task, f"{{{namespace}}}Principals")
        principal = ET.SubElement(principals, f"{{{namespace}}}Principal", {"id": "Author"})
        ET.SubElement(principal, f"{{{namespace}}}RunLevel").text = "LeastPrivilege"
        triggers = ET.SubElement(task, f"{{{namespace}}}Triggers")
        ET.SubElement(triggers, f"{{{namespace}}}LogonTrigger")
        settings = ET.SubElement(task, f"{{{namespace}}}Settings")
        ET.SubElement(settings, f"{{{namespace}}}MultipleInstancesPolicy").text = "IgnoreNew"
        ET.SubElement(settings, f"{{{namespace}}}ExecutionTimeLimit").text = "PT0S"
        ET.SubElement(settings, f"{{{namespace}}}DisallowStartIfOnBatteries").text = "false"
        actions = ET.SubElement(task, f"{{{namespace}}}Actions", {"Context": "Author"})
        execute = ET.SubElement(actions, f"{{{namespace}}}Exec")
        argv = self.command_argv()
        ET.SubElement(execute, f"{{{namespace}}}Command").text = argv[0]
        ET.SubElement(execute, f"{{{namespace}}}Arguments").text = _windows_arguments(argv[1:])
        return ET.tostring(task, encoding="utf-8", xml_declaration=True)

    def macos_plist_bytes(self) -> bytes:
        if self.platform_name not in {"darwin", "macos"}:
            raise ServiceDefinitionError("macos_definition_requested_on_non_macos")
        logs = create_state_paths(self.profile).logs
        return plistlib.dumps(
            {
                "Label": self.service_identity,
                "ProgramArguments": self.command_argv(),
                "RunAtLoad": True,
                "KeepAlive": True,
                "ProcessType": "Background",
                "StandardOutPath": str(logs / "service.out.log"),
                "StandardErrorPath": str(logs / "service.err.log"),
            },
            fmt=plistlib.FMT_XML,
            sort_keys=True,
        )

    def install(self) -> Path:
        path = self.definition_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.platform_name.startswith("win"):
            path.write_bytes(self.windows_task_xml())
            self._run(["schtasks.exe", "/Create", "/TN", self.service_identity, "/XML", str(path), "/F"])
        elif self.platform_name in {"darwin", "macos"}:
            path.write_bytes(self.macos_plist_bytes())
            self._run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
        else:
            raise ServiceDefinitionError("unsupported_platform")
        return path

    def uninstall(self) -> None:
        path = self.definition_path
        if self.platform_name.startswith("win"):
            self._run(["schtasks.exe", "/Delete", "/TN", self.service_identity, "/F"])
        elif self.platform_name in {"darwin", "macos"}:
            self._run(["launchctl", "bootout", f"gui/{os.getuid()}/{self.service_identity}"], allow_failure=True)
        else:
            raise ServiceDefinitionError("unsupported_platform")
        path.unlink(missing_ok=True)

    def status(self) -> bool:
        if self.platform_name.startswith("win"):
            return self._run(["schtasks.exe", "/Query", "/TN", self.service_identity], allow_failure=True).returncode == 0
        if self.platform_name in {"darwin", "macos"}:
            return self.definition_path.is_file()
        raise ServiceDefinitionError("unsupported_platform")

    def _run(self, arguments: list[str], *, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
        completed = self._runner(arguments, text=True, capture_output=True, check=False)
        if completed.returncode != 0 and not allow_failure:
            raise ServiceDefinitionError("service_command_failed")
        return completed


def _windows_arguments(arguments: list[str]) -> str:
    if any("\x00" in argument for argument in arguments):
        raise ServiceDefinitionError("nul_in_service_argument")
    return " ".join('"' + argument.replace('"', '\\"') + '"' for argument in arguments)
