from __future__ import annotations

from collections.abc import Iterator

from modal_sana.core.batcher import build_batches
from modal_sana.core.generator import GenerateRequest, GenerateResult, ImageGenerator
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
        )
        for item in generations
    ]


def run_batches(
    generations: list[GenerationRow],
    generator: ImageGenerator,
    *,
    batch_size: int,
    gpu: str,
    workers: int,
    model: str,
    retry: int,
    deployed: bool,
) -> Iterator[GenerateResult]:
    """Fan-out helper. Modal does the real scheduling via .map()."""
    batches = [
        requests_from_generations(chunk)
        for chunk in build_batches(generations, batch_size)
    ]
    yield from generator.generate_batches(
        batches,
        gpu=gpu,
        workers=workers,
        model=model,
        retry=retry,
        deployed=deployed,
    )
