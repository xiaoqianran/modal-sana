from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from modal_sana.modal.app import app
from modal_sana.modal.image import download_image
from modal_sana.modal.secrets import hf_token
from modal_sana.modal.volumes import CACHE_DIR, huggingface_cache_volume
from modal_sana.modal.weights import download_model_weights, inspect_model_cache, list_ready_models
from modal_sana.models.sana.registry import get_model, list_models

MINUTES = 60


def iter_prefetch_events(
    model_id: str,
    *,
    root: str | Path | None = None,
    token: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield start / progress / cached|done events for one model.

    Complete snapshots emit ``cached`` and never touch the network.
    """
    spec = get_model(model_id)
    started = time.perf_counter()
    info = inspect_model_cache(model_id, root=root)
    base: dict[str, Any] = {
        "model_id": spec.id,
        "hf_id": spec.hf_id,
        "path": info["path"],
        "device": "cpu",
        "bytes": info["bytes"],
    }
    yield {
        **base,
        "event": "start",
        "complete": info["complete"],
        "missing": info["missing"],
    }
    if info["complete"]:
        yield {
            **base,
            "event": "cached",
            "status": "cached",
            "elapsed_ms": (time.perf_counter() - started) * 1000,
        }
        return

    events: queue.Queue[tuple[str, dict[str, Any] | None]] = queue.Queue()
    holder: dict[str, Any] = {}

    def on_progress(payload: dict[str, Any]) -> None:
        events.put(("progress", payload))

    def worker() -> None:
        try:
            holder["result"] = download_model_weights(
                model_id,
                token=token or hf_token(),
                root=root,
                on_progress=on_progress,
            )
        except BaseException as exc:  # noqa: BLE001
            holder["error"] = exc
        finally:
            events.put(("end", None))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while True:
        kind, payload = events.get()
        if kind == "end":
            break
        yield {**base, "event": "progress", **(payload or {})}
    thread.join()
    elapsed_ms = (time.perf_counter() - started) * 1000
    if "error" in holder:
        yield {
            **base,
            "event": "error",
            "status": "error",
            "error": str(holder["error"]),
            "elapsed_ms": elapsed_ms,
        }
        raise holder["error"]
    result = dict(holder["result"])
    result.update(base)
    result["elapsed_ms"] = elapsed_ms
    result["device"] = "cpu"
    result["event"] = "done" if result.get("status") == "downloaded" else "cached"
    yield result


def _consume_prefetch(model_id: str) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for event in iter_prefetch_events(model_id):
        last = event
    result = dict(last or {})
    result.pop("event", None)
    result.setdefault("device", "cpu")
    return result


@app.function(
    image=download_image,
    cpu=4.0,
    memory=8192,
    timeout=60 * MINUTES,
    scaledown_window=10,
    volumes={CACHE_DIR: huggingface_cache_volume()},
)
def prefetch_model(model_id: str) -> dict[str, Any]:
    """Download one SANA snapshot onto the Volume. No GPU.

    Complete snapshots return ``cached`` and do not ``commit()``.
    """
    huggingface_cache_volume().reload()
    result = _consume_prefetch(model_id)
    if result.get("status") == "downloaded":
        huggingface_cache_volume().commit()
    return result


@app.function(
    image=download_image,
    cpu=4.0,
    memory=8192,
    timeout=60 * MINUTES,
    scaledown_window=10,
    volumes={CACHE_DIR: huggingface_cache_volume()},
)
def prefetch_progress(model_id: str):
    """Stream download progress for one model. Used by the local CLI."""
    huggingface_cache_volume().reload()
    downloaded = False
    for event in iter_prefetch_events(model_id):
        if event.get("event") == "done" and event.get("status") == "downloaded":
            downloaded = True
        yield event
    if downloaded:
        huggingface_cache_volume().commit()


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


@app.function(
    image=download_image,
    cpu=0.125,
    timeout=60,
    scaledown_window=10,
)
def registered_model_ids() -> list[str]:
    """Model ids baked into this deployed image. No Volume I/O."""
    return [spec.id for spec in list_models()]
