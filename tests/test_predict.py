from __future__ import annotations

from modal_sana.core.ledger import CostEvent
from modal_sana.core.predict import predict_run


def test_predict_splits_load_and_generate() -> None:
    out = predict_run(
        model="sana-sprint-1.6b",
        gpu="L40S",
        count=2,
        workers=1,
        batch_size=4,
    )
    assert out["gpu"] == "L40S"
    assert out["load"]["usd"] > 0
    assert out["generate"]["usd"] > 0
    assert out["generate"]["count"] == 2
    assert out["load"]["containers"] == 1
    assert abs(out["total_usd"] - (out["load"]["usd"] + out["generate"]["usd"])) < 1e-12


def test_predict_h100_is_not_silently_l40s() -> None:
    out = predict_run(model="sana-1.5-4.8b", gpu="H100", count=1, workers=1)
    assert out["gpu"] == "H100"
    assert out["model"] == "sana-1.5-4.8b"
    assert out["usd_per_second"] > 0.001
    l40s = predict_run(model="sana-1.5-4.8b", gpu="L40S", count=1, workers=1)
    assert l40s["usd_per_second"] < out["usd_per_second"]


def test_predict_uses_ledger_history() -> None:
    history = [
        CostEvent(
            id="evt_a_gpu_load",
            ts="2026-08-16T07:00:00+00:00",
            kind="gpu_load",
            model="sana-sprint-1.6b",
            actual_gpu="L40S",
            load_ms=5_000,
        ),
        CostEvent(
            id="evt_b_gpu_load",
            ts="2026-08-16T07:01:00+00:00",
            kind="gpu_load",
            model="sana-sprint-1.6b",
            actual_gpu="L40S",
            load_ms=7_000,
        ),
    ]
    out = predict_run(
        model="sana-sprint-1.6b",
        gpu="L40S",
        count=1,
        workers=1,
        history=history,
    )
    assert out["load"]["per_container_ms"] == 6_000
    assert out["load"]["source"].startswith("ledger")
