from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from modal_sana.core.doctor import modal_workspace
from modal_sana.modal.links import deployed_app_url

DeployReason = Literal[
    "required",
    "env-required",
    "forced-ephemeral",
    "env-ephemeral",
    "auto-found",
    "auto-missing",
]
DeployPath = Literal["deployed", "ephemeral"]

DEPLOY_COMMAND = "uv run modal deploy -m modal_sana.modal.worker"
WORKER_CLASS = "SanaWorker"
DEFAULT_APP_NAME = "modal-sana"


class DeployedAppMissing(RuntimeError):
    """Raised when a job requires the deployed app and lookup fails."""


def deployed_app_name() -> str:
    return os.environ.get("MODAL_SANA_APP_NAME") or DEFAULT_APP_NAME


def env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def deployed_app_available(app_name: str | None = None) -> tuple[bool, str | None]:
    """True when ``SanaWorker`` exists on the named deployed app.

    Does not cache — a long-lived web process should see a deploy that
    happened after it started. Callers that need many lookups in one
    request should reuse the returned tuple themselves.
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
    """Pick deployed vs ephemeral.

    * ``requested=True`` / ``MODAL_SANA_DEPLOYED=1`` — require the deployed app.
    * ``requested=False`` / ``MODAL_SANA_EPHEMERAL=1`` — force ``app.run()``.
    * otherwise probe: use deployed when ``SanaWorker`` exists.

    ``MODAL_SANA_DEPLOYED=0`` is *not* a force-ephemeral switch. That used to
    be the example-file default and only meant "do not require deploy".
    """
    name = deployed_app_name()
    if requested is False:
        return DeployDecision(False, "forced-ephemeral", name)
    if requested is True:
        return _require(name, "required")
    if env_truthy("MODAL_SANA_EPHEMERAL"):
        return DeployDecision(False, "env-ephemeral", name)
    if env_truthy("MODAL_SANA_DEPLOYED"):
        return _require(name, "env-required")
    available, error = deployed_app_available(name)
    if available:
        return DeployDecision(True, "auto-found", name, available=True)
    return DeployDecision(False, "auto-missing", name, available=False, error=error)


def _require(name: str, reason: DeployReason) -> DeployDecision:
    available, error = deployed_app_available(name)
    if not available:
        hint = error or "SanaWorker not found"
        raise DeployedAppMissing(
            f"Deployed Modal app '{name}' was not found ({hint}). "
            f"Memory snapshots only work on a deployed app, not `modal serve` "
            f"and not an ephemeral `app.run()`. Deploy with:\n\n  {DEPLOY_COMMAND}\n\n"
            "Or pass --ephemeral / uncheck the deployed-app box to use a one-off run."
        )
    return DeployDecision(True, reason, name, available=True)


def inspect_deploy_target() -> dict[str, Any]:
    """Status for /api/meta and Settings. Never raises."""
    name = deployed_app_name()
    available, error = deployed_app_available(name)
    if env_truthy("MODAL_SANA_EPHEMERAL"):
        preference: str = "ephemeral"
        would_use: DeployPath = "ephemeral"
    elif env_truthy("MODAL_SANA_DEPLOYED"):
        preference = "require"
        would_use = "deployed" if available else "ephemeral"
    else:
        preference = "auto"
        would_use = "deployed" if available else "ephemeral"
    if available:
        note = (
            f"Found deployed app '{name}'. Generate/prefetch will call it "
            "(CPU memory snapshots on). This local web is not `modal serve`."
        )
    else:
        note = (
            f"No deployed '{name}' app. Generate will use a one-off ephemeral "
            f"`app.run()` and Modal will disable memory snapshots. Fix: {DEPLOY_COMMAND}"
        )
        if error:
            note = f"{note} ({error})"
    return {
        "app_name": name,
        "available": available,
        "error": error,
        "preference": preference,
        "would_use": would_use,
        "snapshots": would_use == "deployed",
        "note": note,
        "deploy_command": DEPLOY_COMMAND,
        "not_modal_serve": True,
    }
