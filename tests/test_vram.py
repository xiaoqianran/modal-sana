from __future__ import annotations

from modal_sana.core.jobs import JobService
from modal_sana.core.ledger import CostEvent
from modal_sana.core.mock import MockGenerator
from modal_sana.modal.worker import _vram_mb, _vram_stats
from modal_sana.schemas.job import JobConfig, PromptSpec


def test_vram_stats_without_cuda() -> None:
    stats = _vram_stats()
    assert set(stats) == {"vram_allocated_mb", "vram_reserved_mb", "vram_peak_mb"}
    assert stats["vram_allocated_mb"] is None
    assert _vram_mb() is None


def test_cost_event_keeps_vram_fields() -> None:
    event = CostEvent(
        id="evt_test",
        ts="2026-08-16T09:00:00+00:00",
        kind="gpu_generate",
        model="sana-1.6b-2k",
        vram_allocated_mb=9800.0,
        vram_reserved_mb=15200.0,
        vram_peak_mb=16100.0,
    )
    payload = event.as_dict()
    assert payload["vram_allocated_mb"] == 9800.0
    assert payload["vram_reserved_mb"] == 15200.0
    assert payload["vram_peak_mb"] == 16100.0


def test_job_persists_vram_telemetry(service: JobService) -> None:
    class _VramMock(MockGenerator):
        def generate_batches(self, *args, **kwargs):
            for result in super().generate_batches(*args, **kwargs):
                result.telemetry.update(
                    {
                        "vram_allocated_mb": 9800.0,
                        "vram_reserved_mb": 15200.0,
                        "vram_peak_mb": 16100.0,
                    }
                )
                yield result

    job = service.create_job(
        [PromptSpec(prompt="vram persist", count=1, seed=3)],
        JobConfig(dry_run=True, seed=3),
    )
    service.run_job(job.id, generator=_VramMock())
    report = service.cost_report(job.id)
    row = report["by_generation"][0]
    assert row["vram_allocated_mb"] == 9800.0
    assert row["vram_reserved_mb"] == 15200.0
    assert row["vram_peak_mb"] == 16100.0
    detail = service.get_job_detail(job.id)
    assert detail["generations"][0]["vram_reserved_mb"] == 15200.0


def test_job_config_still_defaults_one_worker() -> None:
    assert JobConfig().workers == 1
