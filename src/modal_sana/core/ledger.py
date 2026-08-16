from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

Period = Literal["hour", "day", "week", "month", "all"]
PERIODS: tuple[Period, ...] = ("hour", "day", "week", "month", "all")


@dataclass
class CostEvent:
    id: str
    ts: str
    kind: str
    job_id: str | None = None
    generation_id: str | None = None
    model: str | None = None
    requested_gpu: str | None = None
    actual_gpu: str | None = None
    actual_device: str | None = None
    gpu_seconds: float = 0.0
    cost_usd: float = 0.0
    usd_per_second: float | None = None
    usd_per_hour: float | None = None
    load_ms: float | None = None
    infer_ms: float | None = None
    encode_ms: float | None = None
    vram_allocated_mb: float | None = None
    vram_reserved_mb: float | None = None
    vram_peak_mb: float | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    cold_start: bool = False
    from_snapshot: bool | None = None
    gpu_match: bool | None = None
    model_match: bool | None = None
    modal_function_call_id: str | None = None
    modal_input_id: str | None = None
    modal_task_id: str | None = None
    generation_ids: list[str] | None = None
    workspace: str | None = None
    source: str = "modal-worker"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_EVENT_FIELDS = {item.name for item in fields(CostEvent)}


def event_from_mapping(data: dict[str, Any]) -> CostEvent:
    payload = {key: data.get(key) for key in _EVENT_FIELDS if key in data}
    if "id" not in payload or "ts" not in payload or "kind" not in payload:
        raise ValueError("cost event missing id/ts/kind")
    payload.setdefault("gpu_seconds", 0.0)
    payload.setdefault("cost_usd", 0.0)
    return CostEvent(**payload)  # type: ignore[arg-type]


def parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        ts = value
    else:
        text = value.replace("Z", "+00:00")
        ts = datetime.fromisoformat(text)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def period_bucket(ts: datetime, period: Period) -> str:
    ts = parse_ts(ts)
    if period == "hour":
        return ts.strftime("%Y-%m-%d %H:00 UTC")
    if period == "day":
        return ts.strftime("%Y-%m-%d")
    if period == "week":
        iso = ts.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period == "month":
        return ts.strftime("%Y-%m")
    return "all"


def period_window(period: Period, *, now: datetime | None = None) -> tuple[datetime | None, datetime | None]:
    """Inclusive start for 'this hour/day/week/month'. ``all`` has no window."""
    current = parse_ts(now or datetime.now(timezone.utc))
    if period == "hour":
        start = current.replace(minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=1)
    if period == "day":
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if period == "week":
        start = current.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=current.weekday())
        return start, start + timedelta(days=7)
    if period == "month":
        start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, end
    return None, None


def filter_events(
    events: list[CostEvent],
    *,
    period: Period = "all",
    since: datetime | None = None,
    until: datetime | None = None,
    kind: str | None = None,
    model: str | None = None,
    gpu: str | None = None,
    job_id: str | None = None,
) -> list[CostEvent]:
    start, end = period_window(period) if period != "all" and since is None and until is None else (since, until)
    out: list[CostEvent] = []
    for event in events:
        ts = parse_ts(event.ts)
        if start and ts < start:
            continue
        if end and ts >= end:
            continue
        if kind and event.kind != kind:
            continue
        if model and event.model != model:
            continue
        if gpu and event.actual_gpu != gpu and event.requested_gpu != gpu:
            continue
        if job_id and event.job_id != job_id:
            continue
        out.append(event)
    out.sort(key=lambda item: item.ts, reverse=True)
    return out


def summarize(events: list[CostEvent]) -> dict[str, Any]:
    load = [item for item in events if item.kind == "gpu_load"]
    generate = [item for item in events if item.kind == "gpu_generate"]
    other = [item for item in events if item.kind not in {"gpu_load", "gpu_generate"}]
    return {
        "count": len(events),
        "load_count": len(load),
        "generate_count": len(generate),
        "load_cost_usd": _sum_cost(load),
        "generate_cost_usd": _sum_cost(generate),
        "other_cost_usd": _sum_cost(other),
        "total_cost_usd": _sum_cost(events),
        "gpu_seconds": sum(float(item.gpu_seconds or 0.0) for item in events),
        "load_seconds": sum(float(item.gpu_seconds or 0.0) for item in load),
        "generate_seconds": sum(float(item.gpu_seconds or 0.0) for item in generate),
    }


def period_rows(events: list[CostEvent], period: Period) -> list[dict[str, Any]]:
    buckets: dict[str, list[CostEvent]] = {}
    for event in events:
        key = period_bucket(parse_ts(event.ts), period)
        buckets.setdefault(key, []).append(event)
    rows = []
    for key in sorted(buckets, reverse=True):
        rows.append({"period": key, "grain": period, **summarize(buckets[key])})
    return rows


def paginate(events: list[CostEvent], page: int, per_page: int) -> dict[str, Any]:
    page = max(int(page), 1)
    per_page = min(max(int(per_page), 1), 200)
    total = len(events)
    start = (page - 1) * per_page
    items = events[start : start + per_page]
    return {
        "items": [enrich_event(item) for item in items],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max((total + per_page - 1) // per_page, 1),
    }


def merge_events(*groups: list[CostEvent]) -> list[CostEvent]:
    by_id: dict[str, CostEvent] = {}
    for group in groups:
        for event in group:
            by_id[event.id] = event
    merged = list(by_id.values())
    merged.sort(key=lambda item: item.ts, reverse=True)
    return merged


def enrich_event(event: CostEvent) -> dict[str, Any]:
    """Add $/s, the seconds × rate formula, and the Modal call chain."""
    from modal_sana.core.cost import cost_formula, format_rate
    from modal_sana.modal.gpu import get_gpu

    payload = event.as_dict()
    gpu = event.actual_gpu or event.requested_gpu
    rate = event.usd_per_second
    hour = event.usd_per_hour
    if rate is None and gpu:
        try:
            spec = get_gpu(gpu)
            rate = spec.usd_per_second
            hour = spec.usd_per_hour
        except ValueError:
            rate = None
            hour = None
    seconds = float(event.gpu_seconds or 0.0)
    payload["usd_per_second"] = rate
    payload["usd_per_hour"] = hour
    payload["rate_display"] = format_rate(rate)
    payload["formula"] = cost_formula(gpu, seconds, rate, event.cost_usd)
    payload["chain"] = call_chain(event)
    payload["billed_gpu"] = gpu
    return payload


def call_chain(event: CostEvent) -> list[dict[str, Any]]:
    gpu = event.actual_gpu or event.requested_gpu or "?"
    seconds = float(event.gpu_seconds or 0.0)
    kind_label = {
        "gpu_load": "加载权重",
        "gpu_generate": "推理+编码",
    }.get(event.kind, event.kind)
    steps: list[dict[str, Any]] = [
        {"name": "modal-sana", "kind": "app", "detail": event.source or "modal-worker"},
    ]
    if event.modal_task_id:
        steps.append({"name": "container", "kind": "task", "detail": event.modal_task_id})
    if event.modal_function_call_id:
        steps.append(
            {
                "name": "SanaWorker.generate_batch",
                "kind": "function_call",
                "detail": event.modal_function_call_id,
            }
        )
    if event.modal_input_id:
        steps.append({"name": "input", "kind": "input", "detail": event.modal_input_id})
    steps.append(
        {
            "name": kind_label,
            "kind": event.kind,
            "detail": f"{gpu} · {seconds:.4f}s",
            "gpu_seconds": seconds,
            "cost_usd": event.cost_usd,
            "usd_per_second": event.usd_per_second,
        }
    )
    if event.generation_id:
        steps.append({"name": "generation", "kind": "generation", "detail": event.generation_id})
    ids = [item for item in (event.generation_ids or []) if item and item != event.generation_id]
    for extra_id in ids:
        steps.append({"name": "generation", "kind": "generation", "detail": extra_id})
    if event.job_id:
        steps.append({"name": "job", "kind": "job", "detail": event.job_id})
    return steps


def _sum_cost(events: list[CostEvent]) -> float:
    return float(sum(float(item.cost_usd or 0.0) for item in events))
