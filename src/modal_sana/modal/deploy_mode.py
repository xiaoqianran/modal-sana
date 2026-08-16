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
    "auto-redeployed",
    "quota-ephemeral",
]
DeployPath = Literal["deployed", "ephemeral"]

DEPLOY_COMMAND = "uv run modal deploy -m modal_sana.modal.worker"
WORKER_CLASS = "SanaWorker"
DEFAULT_APP_NAME = "modal-sana"

_deploy_lock = threading.RLock()


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


def is_unknown_model_error(exc: BaseException) -> bool:
    """Remote prefetch/worker still running an older registry."""
    return "unknown model" in str(exc).lower()


def deployed_registry_ids(app_name: str | None = None) -> set[str] | None:
    """Model ids the deployed image knows. None if we cannot ask."""
    import modal
    from modal.exception import NotFoundError

    name = app_name or deployed_app_name()
    try:
        ids = modal.Function.from_name(name, "registered_model_ids").remote()
        return {str(item) for item in (ids or [])}
    except (NotFoundError, AttributeError):
        pass
    except Exception:  # noqa: BLE001
        pass
    try:
        rows = modal.Function.from_name(name, "list_volume_models").remote()
    except Exception:  # noqa: BLE001
        return None
    known: set[str] = set()
    for row in rows or []:
        if isinstance(row, dict) and row.get("model_id"):
            known.add(str(row["model_id"]))
    return known


def missing_deployed_models(app_name: str, required: list[str]) -> list[str]:
    """Ids the local CLI wants that the deployed image does not register."""
    needed = [item for item in required if item]
    if not needed:
        return []
    known = deployed_registry_ids(app_name)
    if known is None:
        return []
    return [model_id for model_id in needed if model_id not in known]


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


def ensure_deployed_or_fallback(
    requested: bool | None = None,
    *,
    required_models: list[str] | None = None,
) -> DeployDecision:
    """Find SanaWorker, otherwise deploy it.

    If the deployed image is missing any ``required_models`` (for example
    ``prefetch --all`` asking for 2K/4K after a local upgrade), redeploy
    the same app name so the remote registry matches this checkout.

    Ephemeral ``app.run()`` only if the workspace deploy quota is full
    *and* this app is still not deployed.
    """
    intent = resolve_deploy_mode(requested)
    if not intent.use_deployed:
        return intent
    name = intent.app_name
    available, error = deployed_app_available(name)
    if available:
        return _redeploy_if_registry_stale(
            DeployDecision(True, "auto-found", name, available=True),
            required_models,
        )
    with _deploy_lock:
        available, error = deployed_app_available(name)
        if available:
            return _redeploy_if_registry_stale(
                DeployDecision(True, "auto-found", name, available=True),
                required_models,
            )
        try:
            deploy_local_app(name)
        except Exception as exc:  # noqa: BLE001
            still, still_err = deployed_app_available(name)
            if still:
                return _redeploy_if_registry_stale(
                    DeployDecision(True, "auto-found", name, available=True),
                    required_models,
                )
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


def _redeploy_if_registry_stale(
    decision: DeployDecision,
    required_models: list[str] | None,
) -> DeployDecision:
    if not decision.use_deployed or not required_models:
        return decision
    missing = missing_deployed_models(decision.app_name, required_models)
    if not missing:
        return decision
    with _deploy_lock:
        missing = missing_deployed_models(decision.app_name, required_models)
        if not missing:
            return decision
        print(
            "modal-sana: deployed app is missing "
            f"{', '.join(missing)}; redeploying so the remote registry matches "
            "this checkout (same as modal deploy)",
            flush=True,
        )
        try:
            deploy_local_app(decision.app_name)
        except Exception as exc:  # noqa: BLE001
            raise DeployedAppMissing(
                missing_app_message(decision.app_name, str(exc))
            ) from exc
        return DeployDecision(
            True,
            "auto-redeployed",
            decision.app_name,
            available=True,
        )


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
