from __future__ import annotations

import os
from typing import Any

# Longer / more specific tokens first so "L40S" wins over "L4".
_DEVICE_RULES: tuple[tuple[str, str], ...] = (
    ("B300", "B300"),
    ("B200", "B200"),
    ("H200", "H200"),
    ("H100", "H100"),
    ("RTX PRO 6000", "RTX-PRO-6000"),
    ("RTX 6000 BLACKWELL", "RTX-PRO-6000"),
    ("RTX 6000", "RTX-PRO-6000"),
    ("L40S", "L40S"),
    ("L40 S", "L40S"),
    ("A10G", "A10"),
    ("A10", "A10"),
    ("L4", "L4"),
    ("T4", "T4"),
)


def map_device_name(name: str | None) -> str | None:
    """Map ``torch.cuda.get_device_name`` / nvidia-smi text onto our GPU ids."""
    if not name:
        return None
    compact = name.upper().replace("_", " ").replace("-", " ")
    if "A100" in compact:
        if "80" in compact:
            return "A100-80GB"
        return "A100"
    for token, gpu_id in _DEVICE_RULES:
        if token in compact:
            return gpu_id
    return None


def cuda_device_name() -> str | None:
    try:
        import torch

        if torch.cuda.is_available():
            return str(torch.cuda.get_device_name(0))
    except Exception:
        return None
    return None


def probe_runtime(
    *,
    requested_gpu: str | None = None,
    requested_model: str | None = None,
    loaded_model: str | None = None,
) -> dict[str, Any]:
    requested_gpu = requested_gpu or os.environ.get("MODAL_SANA_REQUESTED_GPU")
    requested_model = requested_model or os.environ.get("MODAL_SANA_REQUESTED_MODEL")
    device = cuda_device_name()
    actual_gpu = map_device_name(device)
    gpu_match = None
    if requested_gpu and actual_gpu:
        gpu_match = requested_gpu == actual_gpu
    model_match = None
    if requested_model and loaded_model:
        model_match = requested_model == loaded_model
    return {
        "requested_gpu": requested_gpu,
        "actual_gpu": actual_gpu,
        "actual_device": device,
        "gpu_match": gpu_match,
        "requested_model": requested_model,
        "loaded_model": loaded_model,
        "model_match": model_match,
        "modal_task_id": os.environ.get("MODAL_TASK_ID"),
        "modal_region": os.environ.get("MODAL_REGION"),
        "modal_cloud": os.environ.get("MODAL_CLOUD_PROVIDER"),
    }
