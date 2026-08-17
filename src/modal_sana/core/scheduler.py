from __future__ import annotations

from collections.abc import Iterator

from modal_sana.core.batcher import build_batches
from modal_sana.core.generator import GenerateRequest, GenerateResult, ImageGenerator
from modal_sana.modal.gpu import resolve_batch_size
from modal_sana.storage.database import GenerationRow


def requests_from_generations(generations: list[GenerationRow]) -> list[GenerateRequest]:
    return [
        GenerateRequest(
            generation_id=item.id,
            prompt=item.prompt,
            negative_prompt=item.negative_prompt,
            seed=item.seed,
            width=item.width,
            height=item.height,
            steps=item.steps,
            guidance=item.guidance,
            model=item.model,
            image_format=item.image_format,
            quality=item.quality,
            job_id=item.job_id,
            requested_gpu=item.gpu,
        )
        for item in generations
    ]


def run_batches(
    generations: list[GenerationRow],
    generator: ImageGenerator,
    *,
    batch_size: int | None,
    gpu: str,
    workers: int,
    model: str,
    retry: int,
    deployed: bool | None,
) -> Iterator[GenerateResult]:
    """Fan-out helper. Modal does the real scheduling via .map().

    Resolve auto batching here as the final backend guard so direct JobService
    callers, Web/API clients and CLI all share the same model × GPU policy.
    """
    resolved_batch = resolve_batch_size(model, gpu, batch_size)
    batches = [
        requests_from_generations(chunk)
        for chunk in build_batches(generations, resolved_batch)
    ]
    yield from generator.generate_batches(
        batches,
        gpu=gpu,
        workers=workers,
        model=model,
        retry=retry,
        deployed=deployed,
    )
