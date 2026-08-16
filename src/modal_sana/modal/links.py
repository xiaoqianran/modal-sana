from __future__ import annotations


def app_run_url(app_id: str | None, workspace: str | None = None) -> str | None:
    """Dashboard URL for an ephemeral or deployed app run."""
    if not app_id:
        return None
    handle = (workspace or "").strip() or "unknown"
    return f"https://modal.com/apps/{handle}/main/{app_id}"
