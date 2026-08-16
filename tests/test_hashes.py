from __future__ import annotations

from modal_sana.core.hashes import normalize_prompt, task_hash


def test_normalize_prompt() -> None:
    assert normalize_prompt("  A Cat   Sitting ") == "a cat sitting"


def test_task_hash_stable() -> None:
    kwargs = dict(
        prompt="A Cat",
        negative_prompt="",
        seed=1,
        model="sana-sprint-1.6b",
        width=1024,
        height=1024,
        steps=2,
        guidance=4.5,
        image_format="webp",
    )
    assert task_hash(**kwargs) == task_hash(**kwargs)
    assert task_hash(**kwargs) != task_hash(**{**kwargs, "seed": 2})
