from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import modal

from modal_sana.core.ledger import (
    CostEvent,
    Period,
    event_from_mapping,
    filter_events,
    merge_events,
    paginate,
    period_rows,
    summarize,
)
from modal_sana.modal.app import app
from modal_sana.modal.image import download_image
from modal_sana.modal.volumes import (
    LEDGER_DIR,
    LEDGER_DICT_NAME,
    cost_ledger_volume,
)

MINUTES = 60


def cost_ledger_dict() -> Any:
    return modal.Dict.from_name(LEDGER_DICT_NAME, create_if_missing=True)


def record_cost_events(events: list[dict[str, Any]]) -> int:
    """Write events to the workspace Dict. Any device with this token can read them."""
    if not events:
        return 0
    store = cost_ledger_dict()
    written = 0
    for raw in events:
        if not raw.get("id"):
            continue
        store[str(raw["id"])] = raw
        written += 1
    return written


def load_dict_events(*, refresh: bool = True) -> list[CostEvent]:
    store = cost_ledger_dict()
    events: list[CostEvent] = []
    for key, value in store.items():
        if not str(key).startswith("evt_"):
            continue
        if not isinstance(value, dict):
            continue
        try:
            events.append(event_from_mapping(value))
        except Exception:
            continue
    if refresh:
        for event in events:
            store[event.id] = event.as_dict()
    return events


def safe_query_shared_ledger(**kwargs: Any) -> dict[str, Any]:
    try:
        return query_shared_ledger(**kwargs)
    except Exception as exc:  # noqa: BLE001 — generate page still renders
        period = kwargs.get("period") or "all"
        page = int(kwargs.get("page") or 1)
        per_page = int(kwargs.get("per_page") or 25)
        empty = summarize([])
        return {
            "period": period,
            "summary": empty,
            "snapshots": {grain: empty for grain in ("hour", "day", "week", "month", "all")},
            "periods": [],
            "items": [],
            "total": 0,
            "page": page,
            "per_page": per_page,
            "pages": 1,
            "source": {"dict": False, "volume": False, "volume_error": None},
            "error": f"{type(exc).__name__}: {exc}",
        }


def query_shared_ledger(
    *,
    period: Period = "all",
    page: int = 1,
    per_page: int = 25,
    kind: str | None = None,
    model: str | None = None,
    gpu: str | None = None,
    include_volume: bool = True,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Merge Dict (live, all devices) + Volume archive (durable)."""
    groups = [load_dict_events(refresh=True)]
    volume_error = None
    if include_volume:
        try:
            groups.append(_load_volume_events())
        except Exception as exc:  # noqa: BLE001
            volume_error = f"{type(exc).__name__}: {exc}"
    events = merge_events(*groups)
    filtered = filter_events(events, period=period, kind=kind, model=model, gpu=gpu, job_id=job_id)
    page_data = paginate(filtered, page, per_page)
    snapshots = {
        grain: summarize(filter_events(events, period=grain))  # type: ignore[arg-type]
        for grain in ("hour", "day", "week", "month", "all")
    }
    return {
        "period": period,
        "summary": summarize(filtered),
        "snapshots": snapshots,
        "periods": period_rows(
            filtered if job_id else (events if period == "all" else filtered),
            period if period != "all" else "day",
        ),
        "job_id": job_id,
        "source": {
            "dict": True,
            "volume": volume_error is None and include_volume,
            "volume_error": volume_error,
        },
        **page_data,
    }


def _load_volume_events() -> list[CostEvent]:
    try:
        fn = modal.Function.from_name(
            __import__("os").environ.get("MODAL_SANA_APP_NAME", "modal-sana"),
            "list_cost_events",
        )
        rows = fn.remote()
    except Exception:
        return []
    events: list[CostEvent] = []
    for raw in rows or []:
        if isinstance(raw, dict):
            try:
                events.append(event_from_mapping(raw))
            except Exception:
                continue
    return events


@app.function(
    image=download_image,
    cpu=0.25,
    timeout=2 * MINUTES,
    scaledown_window=10,
    max_containers=1,
    volumes={LEDGER_DIR: cost_ledger_volume()},
)
def archive_cost_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Append events to a daily JSONL on a Volume. Serialized (one container)."""
    if not events:
        return {"written": 0}
    volume = cost_ledger_volume()
    volume.reload()
    root = Path(LEDGER_DIR) / "events"
    root.mkdir(parents=True, exist_ok=True)
    written = 0
    by_day: dict[str, list[dict[str, Any]]] = {}
    for raw in events:
        ts = str(raw.get("ts") or datetime.now(timezone.utc).isoformat())
        day = ts[:10]
        by_day.setdefault(day, []).append(raw)
    for day, rows in by_day.items():
        path = root / f"{day}.jsonl"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        seen = {line.split('"id": "', 1)[-1].split('"', 1)[0] for line in existing.splitlines() if '"id": "' in line}
        with path.open("a", encoding="utf-8") as handle:
            for raw in rows:
                event_id = str(raw.get("id") or "")
                if not event_id or event_id in seen:
                    continue
                handle.write(json.dumps(raw, default=str) + "\n")
                seen.add(event_id)
                written += 1
    volume.commit()
    return {"written": written}


@app.function(
    image=download_image,
    cpu=0.25,
    timeout=2 * MINUTES,
    scaledown_window=10,
    volumes={LEDGER_DIR: cost_ledger_volume()},
)
def list_cost_events() -> list[dict[str, Any]]:
    """Read the durable Volume archive. Used to merge into the live Dict view."""
    cost_ledger_volume().reload()
    root = Path(LEDGER_DIR) / "events"
    if not root.is_dir():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and raw.get("id"):
                events.append(raw)
    return events
