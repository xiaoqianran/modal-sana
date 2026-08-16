from __future__ import annotations

from modal_sana.core.jobs import JobService
from modal_sana.core.mock import MockGenerator
from modal_sana.core.trace import format_span_tree, list_spans
from modal_sana.schemas.job import JobConfig, PromptSpec


def test_dry_run_records_spans_and_zero_cost(service: JobService) -> None:
    job = service.create_job(
        [PromptSpec(prompt="a white cat", count=2, seed=1)],
        JobConfig(dry_run=True, count=2, seed=1, workers=1, batch_size=2),
    )
    final = service.run_job(job.id, generator=MockGenerator())
    assert final.status == "completed"
    assert final.cost_usd == 0
    assert (final.gpu_seconds or 0) == 0

    spans = list_spans(service.db, job.id)
    names = {row.name for row in spans}
    assert "job.create" in names
    assert "job.run" in names
    assert "modal.generate" in names
    assert "persist.image" in names
    assert "modal.map" in names
    by_name = {row.name: row for row in spans if row.name in {"job.run", "modal.map", "modal.generate"}}
    assert by_name["modal.map"].parent_span_id == by_name["job.run"].span_id
    assert by_name["modal.generate"].parent_span_id == by_name["modal.map"].span_id

    detail = service.get_job_detail(job.id)
    assert detail["cost"]["cost_usd"] == 0
    assert len(detail["generations"]) == 2
    assert detail["generations"][0]["infer_ms"] is not None
    tree_lines = format_span_tree(list_spans(service.db, job.id))
    assert any("job.run" in line for line in tree_lines)


def test_cost_report_localizes_generations(service: JobService) -> None:
    job = service.create_job(
        [PromptSpec(prompt="a forest", count=1, seed=7)],
        JobConfig(dry_run=True, seed=7),
    )
    service.run_job(job.id, generator=MockGenerator())
    report = service.cost_report(job.id)
    assert report["gpu"] == "L40S"
    assert len(report["by_generation"]) == 1
    assert report["by_generation"][0]["generation_id"].startswith("gen_")
