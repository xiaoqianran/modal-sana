from __future__ import annotations

from modal_sana.core.batcher import build_batches
from modal_sana.storage.database import GenerationRow


def _gen(id_: str, width: int = 1024) -> GenerationRow:
    return GenerationRow(
        id=id_,
        job_id="job",
        prompt_task_id="ptk",
        prompt="a cat",
        seed=1,
        model="sana-sprint-1.6b",
        gpu="L40S",
        steps=2,
        guidance=4.5,
        width=width,
        height=1024,
        task_hash=id_,
    )


def test_batches_respect_size_and_group_by_shape() -> None:
    items = [_gen("a"), _gen("b"), _gen("c"), _gen("d", width=768)]
    batches = build_batches(items, batch_size=2)
    sizes = sorted(len(batch) for batch in batches)
    assert sizes == [1, 2, 1] or sizes == [2, 1, 1]
    assert any(len(batch) == 1 and batch[0].width == 768 for batch in batches)
