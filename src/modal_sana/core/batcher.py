from __future__ import annotations

from collections import defaultdict

from modal_sana.storage.database import GenerationRow


def build_batches(generations: list[GenerationRow], batch_size: int) -> list[list[GenerationRow]]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    groups: dict[tuple, list[GenerationRow]] = defaultdict(list)
    for generation in generations:
        key = (
            generation.model,
            generation.width,
            generation.height,
            generation.steps,
            round(generation.guidance, 4),
            generation.image_format,
        )
        groups[key].append(generation)
    batches: list[list[GenerationRow]] = []
    for items in groups.values():
        for start in range(0, len(items), batch_size):
            batches.append(items[start : start + batch_size])
    return batches
