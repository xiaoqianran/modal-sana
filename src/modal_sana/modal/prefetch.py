from __future__ import annotations

import time
from typing import Any

from modal_sana.modal.app import app
from modal_sana.modal.image import download_image
from modal_sana.modal.volumes import CACHE_DIR, huggingface_cache_volume
from modal_sana.modal.secrets import hf_token
from modal_sana.modal.weights import download_model_weights, list_ready_models
from modal_sana.models.sana.registry import get_model

MINUTES = 60


@app.function(
    image=download_image,
    cpu=4.0,
    memory=8192,
    timeout=60 * MINUTES,
    scaledown_window=10,
    volumes={CACHE_DIR: huggingface_cache_volume()},
)
def prefetch_model(model_id: str) -> dict[str, Any]:
    """Download one SANA snapshot onto the Volume. No GPU."""
    get_model(model_id)
    started = time.perf_counter()
    result = download_model_weights(model_id, token=hf_token())
    huggingface_cache_volume().commit()
    result["elapsed_ms"] = (time.perf_counter() - started) * 1000
    result["device"] = "cpu"
    return result


@app.function(
    image=download_image,
    cpu=1.0,
    timeout=2 * MINUTES,
    scaledown_window=10,
    volumes={CACHE_DIR: huggingface_cache_volume()},
)
def list_volume_models() -> list[dict[str, Any]]:
    """See which SANA snapshots are already on the Volume."""
    huggingface_cache_volume().reload()
    return list_ready_models()
