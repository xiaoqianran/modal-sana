from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        required = {"Python", "modal", "Modal authenticated"}
        return all(check.ok for check in self.checks if check.name in required)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))


def run_doctor() -> DoctorReport:
    report = DoctorReport()
    version = sys.version.split()[0]
    report.add("Python", sys.version_info >= (3, 12), version)

    uv = shutil.which("uv")
    report.add("uv", uv is not None, uv or "not on PATH (optional)")

    try:
        import modal

        report.add("modal", True, getattr(modal, "__version__", "installed"))
    except Exception as exc:  # noqa: BLE001
        report.add("modal", False, str(exc))
        return report

    token_id, source = _modal_token()
    report.add("Modal authenticated", bool(token_id), source or "run `modal setup`")

    workspace = _modal_workspace()
    if workspace:
        report.add("Modal workspace", True, workspace)
    else:
        report.add("Modal workspace", bool(token_id), workspace or "unknown until first run")

    try:
        import urllib.request

        urllib.request.urlopen("https://modal.com", timeout=5).close()
        report.add("network", True, "modal.com reachable")
    except Exception as exc:  # noqa: BLE001
        report.add("network", False, str(exc))

    return report


def _modal_token() -> tuple[str | None, str]:
    token = None
    source = ""
    try:
        from modal.config import config

        token = config.get("token_id")
        if token:
            source = "modal config"
    except Exception:
        token = None
    if token:
        return str(token), source
    env_path = Path.home() / ".modal.toml"
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        if "token_id" in text:
            return "configured", str(env_path)
        return None, f"{env_path} exists but has no token_id"
    return None, ""


def _modal_workspace() -> str:
    try:
        from modal.config import config

        for key in ("workspace", "workspace_name", "org"):
            value = config.get(key)
            if value:
                return str(value)
    except Exception:
        return ""
    return ""
