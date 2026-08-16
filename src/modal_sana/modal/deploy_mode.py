from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Literal

from modal_sana.core.doctor import modal_workspace
from modal_sana.modal.links import deployed_app_url

DeployReason = Literal[
    "default-deployed",
    "required",
    "env-required",
    "forced-ephemeral",
    "env-ephemeral",
    "auto-found",
    "auto-deployed",
    "quota-ephemeral",
]
DeployPath = Literal["deployed", "ephemeral"]

DEPLOY_COMMAND = "uv run modal deploy -m modal_sana.modal.worker"
WORKER_CLASS = "SanaWorker"
DEFAULT_APP_NAME = "modal-sana"

_deploy_lock = threading.Lock()


class DeployedAppMissing(RuntimeError):
    """Raised when deploy is required and we could neither find nor create the app."""


def deployed_app_name() -> str:
    return os.environ.get("MODAL_SANA_APP_NAME") or DEFAULT_APP_NAME


def env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def deployed_app_available(app_name: str | None = None) -> tuple[bool, str | None]:
    """True when ``SanaWorker`` exists on the named deployed app.

    Only ``NotFoundError`` means "not deployed". Any other exception is
    returned as an error string so the UI can show it.
    """
    import modal
    from modal.exception import NotFoundError

    name = app_name or deployed_app_name()
    try:
        cls = modal.Cls.from_name(name, WORKER_CLASS)
        cls.hydrate()
        return True, None
    except NotFoundError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def is_deploy_quota_exhausted(exc: BaseException) -> bool:
    """True when Modal refused a *new* deploy because the workspace is full."""
    try:
        from modal.exception import ResourceExhaustedError
    except Exception:  # noqa: BLE001
        ResourceExhaustedError = ()  # type: ignore[misc,assignment]
    if ResourceExhaustedError and isinstance(exc, ResourceExhaustedError):
        return True
    text = str(exc).lower()
    needles = (
        "too many apps",
        "too many deployed",
        "maximum number of apps",
        "max number of apps",
        "app limit",
        "apps limit",
        "quota",
        "resource exhausted",
        "resource_exhausted",
    )
    return any(needle in text for needle in needles)


def missing_app_message(app_name: str, detail: str | None = None) -> str:
    hint = detail or "SanaWorker not found"
    return (
        f"Could not use deployed Modal app '{app_name}' ({hint}). "
        f"Tried to deploy it automatically. Deploy by hand with:\n\n  {DEPLOY_COMMAND}\n\n"
        "Ephemeral app.run() is only used when the workspace deploy quota is full "
        "and this app is not already deployed."
    )


def deploy_local_app(app_name: str | None = None) -> dict[str, Any]:
    """Same as ``modal deploy -m modal_sana.modal.worker``."""
    import modal

    from modal_sana.modal.app import app
    from modal_sana.modal.client import ensure_local_app_objects

    ensure_local_app_objects()
    name = app_name or deployed_app_name()
    print(f"modal-sana: deploying '{name}' (this is modal deploy, not app.run())", flush=True)
    with modal.enable_output():
        app.deploy(name=name)
    return {"app_name": name, "app_id": getattr(app, "app_id", None)}


@dataclass(frozen=True)
class DeployDecision:
    use_deployed: bool
    reason: DeployReason
    app_name: str
    available: bool | None = None
    error: str | None = None

    @property
    def mode(self) -> DeployPath:
        return "deployed" if self.use_deployed else "ephemeral"

    @property
    def snapshots(self) -> bool:
        return self.use_deployed

    def as_meta(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "deployed": self.use_deployed,
            "deploy_mode": self.mode,
            "deploy_reason": self.reason,
            "app_name": self.app_name,
            "snapshots": self.snapshots,
        }
        if self.error:
            meta["deploy_error"] = self.error
        if self.use_deployed:
            meta["modal_run_url"] = deployed_app_url(self.app_name, modal_workspace())
        return meta


def resolve_deploy_mode(requested: bool | None = None) -> DeployDecision:
    """User intent only. Does not look up or deploy."""
    name = deployed_app_name()
    if requested is False:
        return DeployDecision(False, "forced-ephemeral", name)
    if env_truthy("MODAL_SANA_EPHEMERAL") and requested is not True:
        return DeployDecision(False, "env-ephemeral", name)
    if requested is True:
        return DeployDecision(True, "required", name)
    if env_truthy("MODAL_SANA_DEPLOYED"):
        return DeployDecision(True, "env-required", name)
    return DeployDecision(True, "default-deployed", name)


def ensure_deployed_or_fallback(requested: bool | None = None) -> DeployDecision:
    """Find SanaWorker, otherwise deploy it.

    Ephemeral ``app.run()`` only if the workspace deploy quota is full
    *and* this app is still not deployed.
    """
    intent = resolve_deploy_mode(requested)
    if not intent.use_deployed:
        return intent
    name = intent.app_name
    available, error = deployed_app_available(name)
    if available:
        return DeployDecision(True, "auto-found", name, available=True)
    with _deploy_lock:
        available, error = deployed_app_available(name)
        if available:
            return DeployDecision(True, "auto-found", name, available=True)
        try:
            deploy_local_app(name)
        except Exception as exc:  # noqa: BLE001
            still, still_err = deployed_app_available(name)
            if still:
                return DeployDecision(True, "auto-found", name, available=True)
            if is_deploy_quota_exhausted(exc):
                print(
                    f"modal-sana: deploy quota full and '{name}' is not deployed; "
                    "falling back to ephemeral app.run()",
                    flush=True,
                )
                return DeployDecision(
                    False,
                    "quota-ephemeral",
                    name,
                    available=False,
                    error=str(exc),
                )
            raise DeployedAppMissing(missing_app_message(name, str(exc))) from exc
        available, error = deployed_app_available(name)
        return DeployDecision(True, "auto-deployed", name, available=available, error=error)


def inspect_deploy_target() -> dict[str, Any]:
    """Status for /api/meta and Settings. Never raises. Does not deploy."""
    name = deployed_app_name()
    available, error = deployed_app_available(name)
    if env_truthy("MODAL_SANA_EPHEMERAL"):
        preference = "ephemeral"
        would_use: DeployPath = "ephemeral"
    else:
        preference = "deployed"
        would_use = "deployed"
    if would_use == "ephemeral":
        note = (
            "MODAL_SANA_EPHEMERAL is set. Generate will use a one-off "
            "`app.run()` and Modal will disable memory snapshots."
        )
    elif available:
        note = (
            f"Found deployed app '{name}'. Generate calls it (CPU memory "
            "snapshots on). This local web is not `modal serve`."
        )
    else:
        note = (
            f"No deployed '{name}' yet. First Generate will run `{DEPLOY_COMMAND}` "
            "for you. Ephemeral app.run() only if the workspace deploy quota is "
            "full and this app is not already there."
        )
        if error:
            note = f"{note} ({error})"
    return {
        "app_name": name,
        "available": available,
        "error": error,
        "preference": preference,
        "would_use": would_use,
        "snapshots": would_use == "deployed" and available,
        "note": note,
        "deploy_command": DEPLOY_COMMAND,
        "not_modal_serve": True,
        "auto_deploy": would_use == "deployed" and not available,
    }
