from __future__ import annotations

from modal_sana.modal.app import app
from modal_sana.modal.client import build_worker_options, ensure_local_app_objects
from modal_sana.modal.runtime import map_device_name


def test_map_device_name_covers_common_cards() -> None:
    assert map_device_name("NVIDIA L40S") == "L40S"
    assert map_device_name("NVIDIA L4") == "L4"
    assert map_device_name("NVIDIA H100 80GB HBM3") == "H100"
    assert map_device_name("NVIDIA A100-SXM4-80GB") == "A100-80GB"
    assert map_device_name("NVIDIA A100-SXM4-40GB") == "A100"
    assert map_device_name("NVIDIA RTX PRO 6000 Blackwell") == "RTX-PRO-6000"
    assert map_device_name(None) is None


def test_worker_options_bind_selected_gpu_and_model() -> None:
    options = build_worker_options(
        gpu="H100",
        workers=3,
        retry=1,
        model="sana-1.5-4.8b",
    )
    assert options["gpu"] == "H100"
    assert options["max_containers"] == 3
    assert options["env"]["MODAL_SANA_REQUESTED_GPU"] == "H100"
    assert options["env"]["MODAL_SANA_REQUESTED_MODEL"] == "sana-1.5-4.8b"
    l40s = build_worker_options(gpu="L40S", workers=1, retry=0, model="sana-sprint-1.6b")
    assert l40s["gpu"] == "L40S"
    assert l40s["env"]["MODAL_SANA_REQUESTED_GPU"] == "L40S"


def test_ensure_app_registers_prefetch_and_worker() -> None:
    ensure_local_app_objects()
    assert "prefetch_model" in app.registered_functions
    assert "list_volume_models" in app.registered_functions
    assert "registered_model_ids" in app.registered_functions
    assert "SanaWorker" in app.registered_classes
