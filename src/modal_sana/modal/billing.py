from __future__ import annotations

from decimal import Decimal
from typing import Any

from modal_sana.core.config import load_settings
from modal_sana.core.doctor import modal_workspace


def _money(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def workspace_balance(*, monthly_credits_usd: float | None = None) -> dict[str, Any]:
    """This-month Modal spend + an estimate of remaining credits.

    Modal's public API exposes metered / billed / credit *adjustments*, not the
    unused credit pool. Remaining is ``monthly_credits - metered`` when a
    monthly credit budget is configured (default $30 Starter, override with
    ``MODAL_SANA_MONTHLY_CREDITS``).
    """
    settings = load_settings()
    budget = monthly_credits_usd
    if budget is None:
        budget = settings.monthly_credits_usd
    payload: dict[str, Any] = {
        "ok": False,
        "workspace": modal_workspace() or None,
        "usage_url": "https://modal.com/settings/usage",
        "monthly_credits_usd": budget,
        "metered_usd": None,
        "billed_usd": None,
        "credits_applied_usd": None,
        "remaining_usd": None,
        "remaining_is_estimate": True,
        "cycle_start": None,
        "cycle_end": None,
        "adjustments": {},
        "breakdown": {},
        "error": None,
        "notes": "",
    }
    try:
        import modal

        summary = modal.Workspace.from_context().billing.summary()
    except Exception as exc:  # noqa: BLE001 — surface to the generate page
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["notes"] = "Could not read Modal billing. Check `modal token` / proxy."
        return payload

    adjustments = {str(key): _money(value) for key, value in dict(summary.adjustments).items()}
    breakdown = {str(key): _money(value) for key, value in dict(summary.metered_cost_breakdown).items()}
    metered = _money(summary.metered_cost)
    billed = _money(summary.billed_cost)
    credits = abs(adjustments.get("Credits", 0.0))
    remaining = None
    if budget is not None:
        remaining = max(float(budget) - metered, 0.0)
    payload.update(
        {
            "ok": True,
            "metered_usd": metered,
            "billed_usd": billed,
            "credits_applied_usd": credits,
            "remaining_usd": remaining,
            "cycle_start": summary.start.isoformat() if summary.start else None,
            "cycle_end": summary.end.isoformat() if summary.end else None,
            "adjustments": adjustments,
            "breakdown": breakdown,
            "notes": (
                "Remaining is monthly credits minus this month's metered spend. "
                "Modal does not publish unused credit balance on the API. "
                "Invoice truth: modal.com/settings/usage or `modal billing summary`."
            ),
        }
    )
    return payload
