from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


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

    extra_ok, extra_detail = _proxy_extra()
    report.add("api-proxy-support", extra_ok, extra_detail)

    proxy_ok, proxy_detail = _proxy_env()
    report.add("Modal API proxy", proxy_ok, proxy_detail)

    try:
        import urllib.request

        urllib.request.urlopen("https://modal.com", timeout=5).close()
        report.add("network", True, "modal.com reachable")
    except Exception as exc:  # noqa: BLE001
        report.add("network", False, str(exc))

    try:
        from modal_sana.modal.deploy_mode import DEPLOY_COMMAND, deployed_app_available, deployed_app_name

        name = deployed_app_name()
        found, detail = deployed_app_available(name)
        if found:
            report.add("Deployed app", True, f"{name} · SanaWorker (snapshots on)")
        else:
            report.add(
                "Deployed app",
                False,
                f"{name} missing — Generate will error until you deploy. {DEPLOY_COMMAND}"
                + (f" ({detail})" if detail else ""),
            )
    except Exception as exc:  # noqa: BLE001
        report.add("Deployed app", False, str(exc))

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


def modal_workspace() -> str:
    try:
        from modal.config import config

        for key in ("workspace", "workspace_name", "org"):
            value = config.get(key)
            if value:
                return str(value)
    except Exception:
        return ""
    return ""


def _modal_workspace() -> str:
    return modal_workspace()


def _proxy_extra() -> tuple[bool, str]:
    missing: list[str] = []
    try:
        import python_socks  # noqa: F401
    except Exception:
        missing.append("python-socks")
    try:
        import aiohttp_socks  # noqa: F401
    except Exception:
        missing.append("aiohttp-socks")
    if missing:
        return False, f"missing {', '.join(missing)}; install modal[api-proxy-support]"
    return True, "python-socks + aiohttp-socks"


def _proxy_env() -> tuple[bool, str]:
    disabled = os.environ.get("MODAL_DISABLE_API_PROXY", "")
    if disabled.lower() in {"1", "true", "yes"}:
        return True, "MODAL_DISABLE_API_PROXY set — client will not use a proxy"
    keys = (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    )
    found = [f"{key}={_redact_proxy(os.environ[key])}" for key in keys if os.environ.get(key)]
    if found:
        return True, "; ".join(found)
    return True, "no proxy env (direct to Modal)"


def _redact_proxy(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{host}{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return url
