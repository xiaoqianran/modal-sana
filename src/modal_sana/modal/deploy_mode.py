from __future__ import annotations

import os
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

    Only ``NotFoundError`` means "not deployed". Any other exception is
    returned as an error string so the UI can show it — the generate path
    must not treat those as "use ephemeral".
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


def missing_app_message(app_name: str, detail: str | None = None) -> str:
    hint = detail or "SanaWorker not found"
    return (
        f"Deployed Modal app '{app_name}' was not found ({hint}). "
        f"Generate does not use `modal serve` and will not silently start "
        f"an ephemeral `app.run()` (that path disables memory snapshots). "
        f"Deploy with:\n\n  {DEPLOY_COMMAND}\n\n"
        "Or pass --ephemeral / uncheck the deployed-app box for a one-off run."
    )


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

    Default is **deployed**. There is no silent fallback to ``app.run()`` —
    that is what printed "Memory snapshots are disabled for ephemeral apps"
    after a successful ``modal deploy``.

    * ``requested=False`` / ``MODAL_SANA_EPHEMERAL=1`` — force ``app.run()``.
    * anything else — call the deployed app. Missing app fails at first use.
    """
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


def inspect_deploy_target() -> dict[str, Any]:
    """Status for /api/meta and Settings. Never raises."""
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
            f"MODAL_SANA_EPHEMERAL is set. Generate will use a one-off "
            f"`app.run()` and Modal will disable memory snapshots."
        )
    elif available:
        note = (
            f"Found deployed app '{name}'. Generate calls it (CPU memory "
            "snapshots on). This local web is not `modal serve`."
        )
    else:
        note = (
            f"No deployed '{name}' app yet. Generate will error until you run "
            f"{DEPLOY_COMMAND} — it will not silently use ephemeral."
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
    }
