from __future__ import annotations

from pathlib import Path
from typing import Any

from modal_sana.modal.volumes import MODELS_DIR
from modal_sana.models.sana.registry import get_model, list_models


def local_model_path(model_id: str, *, root: str | Path | None = None) -> Path:
    """On-volume directory for one SANA snapshot. GPU loads only from here."""
    return Path(root or MODELS_DIR) / model_id


def is_model_ready(model_id: str, *, root: str | Path | None = None) -> bool:
    """True when a diffusers snapshot is complete enough to load offline."""
    return (local_model_path(model_id, root=root) / "model_index.json").is_file()


def models_to_prefetch(model: str | None, *, all_models: bool = False) -> list[str]:
    """Default is every registered SANA model. A name pins one snapshot."""
    if (model or "").strip():
        return [get_model(model.strip()).id]
    if all_models:
        return [spec.id for spec in list_models()]
    return [spec.id for spec in list_models() if spec.prefetch_by_default]


def assert_model_ready(model_id: str, *, root: str | Path | None = None) -> Path:
    path = local_model_path(model_id, root=root)
    if not is_model_ready(model_id, root=root):
        raise FileNotFoundError(
            f"SANA weights for {model_id!r} are not on the Modal volume at {path}. "
            "Download them on CPU with `modal-sana prefetch` "
            "(or wait for the automatic CPU prefetch before generate)."
        )
    return path


def download_model_weights(
    model_id: str,
    *,
    token: str | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Fetch one Hugging Face snapshot onto the volume. CPU-only; no torch."""
    spec = get_model(model_id)
    dest = local_model_path(model_id, root=root)
    dest.mkdir(parents=True, exist_ok=True)
    if is_model_ready(model_id, root=root):
        return {
            "model_id": spec.id,
            "hf_id": spec.hf_id,
            "status": "cached",
            "path": str(dest),
        }
    from modal_sana.modal.fast_download import download_hf_repo
    from modal_sana.modal.secrets import hf_token

    method = download_hf_repo(spec.hf_id, dest, token=token or hf_token())
    if not is_model_ready(model_id, root=root):
        raise RuntimeError(
            f"Downloaded {spec.hf_id} to {dest} but model_index.json is missing"
        )
    return {
        "model_id": spec.id,
        "hf_id": spec.hf_id,
        "status": "downloaded",
        "path": str(dest),
        "method": method,
    }


def list_ready_models(*, root: str | Path | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in list_models():
        path = local_model_path(spec.id, root=root)
        ready = is_model_ready(spec.id, root=root)
        size = _dir_bytes(path) if path.exists() else 0
        rows.append(
            {
                "model_id": spec.id,
                "hf_id": spec.hf_id,
                "ready": ready,
                "path": str(path),
                "bytes": size,
            }
        )
    return rows


def _dir_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total
