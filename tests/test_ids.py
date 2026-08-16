from __future__ import annotations

from modal_sana.core.ids import new_id, ulid


def test_ulid_length_and_prefix() -> None:
    value = ulid()
    assert len(value) == 26
    job_id = new_id("job")
    assert job_id.startswith("job_")
    assert len(job_id) == 30
