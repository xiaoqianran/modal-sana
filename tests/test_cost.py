from __future__ import annotations

from modal_sana.core.cost import cost_for_seconds, cost_formula, format_rate, format_usd, item_gpu_seconds
from modal_sana.modal.gpu import estimate_cost_usd
from modal_sana.modal.worker import (
    MEMORY_SNAPSHOT,
    SCALEDOWN_SECONDS,
    SanaWorker,
    detect_snapshot_restore,
)


def test_item_gpu_seconds_cold_vs_warm() -> None:
    cold = item_gpu_seconds(
        load_ms=1000,
        infer_ms=2000,
        encode_ms=500,
        cold_start=True,
        group_size=1,
    )
    warm = item_gpu_seconds(
        load_ms=1000,
        infer_ms=2000,
        encode_ms=500,
        cold_start=False,
        group_size=1,
    )
    assert abs(cold - 3.5) < 1e-9
    assert abs(warm - 2.5) < 1e-9


def test_item_gpu_seconds_splits_load_across_call() -> None:
    seconds = item_gpu_seconds(
        load_ms=4000,
        infer_ms=2000,
        encode_ms=0,
        cold_start=True,
        group_size=2,
        load_group_size=4,
    )
    # load 4000ms / 4 + infer 2000ms / 2 = 1000 + 1000 = 2.0s
    assert abs(seconds - 2.0) < 1e-9


def test_l40s_list_price() -> None:
    assert estimate_cost_usd("L40S", 1.0) == 0.000542
    assert abs(cost_for_seconds("L40S", 10) - 0.00542) < 1e-12
    text = format_usd(0.00542)
    assert "$0.005420" in text
    assert "¢" in text
    assert "$0.000542/s" in format_rate(0.000542)
    assert "L40S · 10.0000s × $0.000542/s" in cost_formula("L40S", 10, 0.000542, 0.00542)


def test_scaledown_is_ten_seconds() -> None:
    assert SCALEDOWN_SECONDS == 10


def test_cpu_memory_snapshot_is_enabled() -> None:
    assert MEMORY_SNAPSHOT is True
    assert hasattr(SanaWorker, "load_cpu")
    assert hasattr(SanaWorker, "load_gpu")


def test_detect_snapshot_restore_uses_monotonic_reset() -> None:
    assert detect_snapshot_restore(120.0, 0.4) is True
    assert detect_snapshot_restore(10.0, 28.0) is False
    assert detect_snapshot_restore(None, 1.0) is True
