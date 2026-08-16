from __future__ import annotations

from modal_sana.core.generator import GenerateResult
from modal_sana.core.mock import MockGenerator
from modal_sana.core.jobs import JobService
from modal_sana.schemas.job import JobConfig, PromptSpec


class FailAll:
    def generate_batches(self, batches, **kwargs):
        for batch in batches:
            for request in batch:
                yield GenerateResult(generation_id=request.generation_id, error="boom")


def test_create_and_run_dry(service: JobService) -> None:
    job = service.create_job(
        [PromptSpec(prompt="a white cat", count=3, seed=10)],
        JobConfig(dry_run=True, count=3, seed=10, workers=1, batch_size=2),
    )
    assert job.total_images == 3
    final = service.run_job(job.id, generator=MockGenerator())
    assert final.status == "completed"
    assert final.completed_images == 3
    page = service.list_images(job_id=job.id)
    assert page.total == 3
    assert page.items[0].seed in {10, 11, 12}
    assert (service.settings.outputs_dir / job.id / "metadata.jsonl").exists()
    assert final.cost_usd == 0
    page_item = page.items[0]
    assert page_item.infer_ms is not None
    assert page_item.cost_usd == 0


def test_deduplicate(service: JobService) -> None:
    job = service.create_job(
        [
            PromptSpec(prompt="a cat", count=1, seed=1),
            PromptSpec(prompt="a cat", count=1, seed=1),
        ],
        JobConfig(dry_run=True, deduplicate=True, seed=1),
    )
    assert job.total_images == 1


class Recorder(MockGenerator):
    def generate_batches(self, batches, **kwargs):
        self.seen = kwargs
        self.requests = [request for batch in batches for request in batch]
        yield from super().generate_batches(batches, **kwargs)


def test_job_forwards_gpu_model_and_image_params(service: JobService) -> None:
    recorder = Recorder()
    job = service.create_job(
        [PromptSpec(prompt="param check", count=1, seed=9)],
        JobConfig(
            model="sana-1.5-4.8b",
            gpu="H100",
            width=512,
            height=768,
            steps=8,
            guidance=3.5,
            dry_run=True,
            seed=9,
            workers=1,
            batch_size=1,
        ),
    )
    final = service.run_job(job.id, generator=recorder)
    assert final.status == "completed"
    assert recorder.seen["gpu"] == "H100"
    assert recorder.seen["model"] == "sana-1.5-4.8b"
    assert recorder.seen["deployed"] is None
    request = recorder.requests[0]
    assert request.model == "sana-1.5-4.8b"
    assert request.requested_gpu == "H100"
    assert request.width == 512
    assert request.height == 768
    assert request.steps == 8
    assert request.guidance == 3.5
    assert request.seed == 9


def test_resume_failed(service: JobService) -> None:
    job = service.create_job(
        [PromptSpec(prompt="a forest", count=2, seed=1)],
        JobConfig(dry_run=True, retry=0, seed=1),
    )
    failed = service.run_job(job.id, generator=FailAll())
    assert failed.status == "failed"
    assert failed.completed_images == 0
    recovered = service.resume(job.id, generator=MockGenerator())
    assert recovered.status == "completed"
    assert recovered.completed_images == 2
