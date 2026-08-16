from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlmodel import col, select

from modal_sana.core.cost import format_usd
from modal_sana.core.ids import new_id
from modal_sana.storage.database import Database, TraceSpanRow, now


def write_span(
    db: Database,
    *,
    name: str,
    job_id: str,
    kind: str = "local",
    parent_span_id: str | None = None,
    generation_id: str | None = None,
    status: str = "ok",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    duration_ms: float | None = None,
    gpu: str | None = None,
    model: str | None = None,
    modal_function_call_id: str | None = None,
    modal_input_id: str | None = None,
    modal_app_id: str | None = None,
    cost_usd: float | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    span_id = new_id("spn")
    started = started_at or now()
    ended = ended_at or now()
    if duration_ms is None:
        duration_ms = max((ended - started).total_seconds() * 1000.0, 0.0)
    with db.session() as session:
        session.add(
            TraceSpanRow(
                span_id=span_id,
                parent_span_id=parent_span_id,
                job_id=job_id,
                generation_id=generation_id,
                name=name,
                kind=kind,
                status=status,
                started_at=started,
                ended_at=ended,
                duration_ms=duration_ms,
                gpu=gpu,
                model=model,
                modal_function_call_id=modal_function_call_id,
                modal_input_id=modal_input_id,
                modal_app_id=modal_app_id,
                cost_usd=cost_usd,
                extra_json=json.dumps(extra or {}, default=str),
            )
        )
    return span_id


@contextmanager
def span(
    db: Database,
    name: str,
    job_id: str,
    *,
    kind: str = "local",
    parent_span_id: str | None = None,
    generation_id: str | None = None,
    gpu: str | None = None,
    model: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Insert a running span immediately so children can parent to it."""
    span_id = new_id("spn")
    started_at = now()
    t0 = time.perf_counter()
    fields: dict[str, Any] = {
        "span_id": span_id,
        "status": "ok",
        "cost_usd": None,
        "modal_function_call_id": None,
        "modal_input_id": None,
        "modal_app_id": None,
        "extra": dict(extra or {}),
    }
    with db.session() as session:
        session.add(
            TraceSpanRow(
                span_id=span_id,
                parent_span_id=parent_span_id,
                job_id=job_id,
                generation_id=generation_id,
                name=name,
                kind=kind,
                status="running",
                started_at=started_at,
                ended_at=None,
                duration_ms=None,
                gpu=gpu,
                model=model,
                extra_json=json.dumps(fields["extra"], default=str),
            )
        )
    try:
        yield fields
    except Exception:
        fields["status"] = "error"
        raise
    finally:
        with db.session() as session:
            row = session.get(TraceSpanRow, span_id)
            if row is None:
                return
            row.status = str(fields.get("status") or "ok")
            row.ended_at = now()
            row.duration_ms = (time.perf_counter() - t0) * 1000.0
            row.modal_function_call_id = fields.get("modal_function_call_id")
            row.modal_input_id = fields.get("modal_input_id")
            row.modal_app_id = fields.get("modal_app_id")
            row.cost_usd = fields.get("cost_usd")
            row.extra_json = json.dumps(fields.get("extra") or {}, default=str)


def list_spans(db: Database, job_id: str) -> list[TraceSpanRow]:
    with db.session() as session:
        statement = (
            select(TraceSpanRow)
            .where(TraceSpanRow.job_id == job_id)
            .order_by(col(TraceSpanRow.started_at).asc())
        )
        return list(session.exec(statement))


def span_as_dict(row: TraceSpanRow) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if row.extra_json:
        try:
            extra = json.loads(row.extra_json)
        except json.JSONDecodeError:
            extra = {"raw": row.extra_json}
    return {
        "span_id": row.span_id,
        "parent_span_id": row.parent_span_id,
        "job_id": row.job_id,
        "generation_id": row.generation_id,
        "name": row.name,
        "kind": row.kind,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "duration_ms": row.duration_ms,
        "gpu": row.gpu,
        "model": row.model,
        "modal_function_call_id": row.modal_function_call_id,
        "modal_input_id": row.modal_input_id,
        "modal_app_id": row.modal_app_id,
        "cost_usd": row.cost_usd,
        "extra": extra,
    }


def span_tree(rows: list[TraceSpanRow]) -> list[dict[str, Any]]:
    nodes = {row.span_id: {**span_as_dict(row), "children": []} for row in rows}
    roots: list[dict[str, Any]] = []
    for row in rows:
        node = nodes[row.span_id]
        parent = nodes.get(row.parent_span_id or "")
        if parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots


def format_span_tree(rows: list[TraceSpanRow]) -> list[str]:
    lines: list[str] = []

    def walk(node: dict[str, Any], prefix: str, last: bool) -> None:
        branch = "└─ " if last else "├─ "
        if not prefix:
            branch = ""
        dur = node.get("duration_ms")
        dur_s = f"{dur / 1000:.3f}s" if dur is not None else "—"
        cost = format_usd(node.get("cost_usd")) if node.get("cost_usd") else ""
        loc = node.get("modal_input_id") or node.get("generation_id") or ""
        bits = [node["name"], dur_s, node.get("kind") or ""]
        if cost:
            bits.append(cost)
        if loc:
            bits.append(loc)
        lines.append(f"{prefix}{branch}{'  '.join(b for b in bits if b)}")
        children = node.get("children") or []
        child_prefix = prefix + ("   " if last or not prefix else "│  ")
        for i, child in enumerate(children):
            walk(child, child_prefix, i == len(children) - 1)

    tree = span_tree(rows)
    for i, root in enumerate(tree):
        walk(root, "", i == len(tree) - 1)
    return lines


def cost_breakdown(rows: list[TraceSpanRow]) -> dict[str, Any]:
    by_name: dict[str, float] = defaultdict(float)
    by_generation: dict[str, float] = defaultdict(float)
    total = 0.0
    for row in rows:
        if row.cost_usd is None:
            continue
        by_name[row.name] += row.cost_usd
        if row.generation_id:
            by_generation[row.generation_id] += row.cost_usd
        if row.name == "modal.generate":
            total += row.cost_usd
    if total == 0.0:
        total = sum(by_generation.values()) or sum(by_name.values())
    return {
        "cost_usd": total,
        "by_name": dict(by_name),
        "by_generation": dict(by_generation),
    }
