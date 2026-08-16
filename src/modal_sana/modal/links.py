from __future__ import annotations


def app_run_url(app_id: str | None, workspace: str | None = None) -> str | None:
    """Dashboard URL for an ephemeral or deployed app run."""
    if not app_id:
        return None
    handle = (workspace or "").strip() or "unknown"
    return f"https://modal.com/apps/{handle}/main/{app_id}"


def deployed_app_url(app_name: str | None, workspace: str | None = None) -> str | None:
    """Dashboard URL for a named deployed app (not an ephemeral ap- id)."""
    if not app_name:
        return None
    handle = (workspace or "").strip() or "unknown"
    return f"https://modal.com/apps/{handle}/main/{app_name}"
