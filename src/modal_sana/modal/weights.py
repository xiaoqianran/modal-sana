from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modal_sana.modal.volumes import MODELS_DIR
from modal_sana.models.sana.registry import get_model, list_models

INCOMPLETE_SUFFIXES = (".aria2", ".incomplete", ".tmp")
WEIGHT_SUFFIXES = {".safetensors", ".bin"}
WEIGHT_COMPONENTS = {"transformer", "vae", "text_encoder", "text_encoder_2"}


def local_model_path(model_id: str, *, root: str | Path | None = None) -> Path:
    """On-volume directory for one SANA snapshot. GPU loads only from here."""
    return Path(root or MODELS_DIR) / model_id


def inspect_model_cache(model_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    """Local completeness of one snapshot. Does not touch the network."""
    spec = get_model(model_id)
    dest = local_model_path(model_id, root=root)
    bytes_ = _dir_bytes(dest) if dest.exists() else 0
    missing = _missing_snapshot_parts(dest)
    complete = not missing
    return {
        "model_id": spec.id,
        "hf_id": spec.hf_id,
        "path": str(dest),
        "ready": complete,
        "complete": complete,
        "missing": missing,
        "bytes": bytes_,
    }


def is_model_ready(model_id: str, *, root: str | Path | None = None) -> bool:
    """True only when the diffusers snapshot is complete enough to load offline."""
    return inspect_model_cache(model_id, root=root)["complete"]


def models_to_prefetch(model: str | None, *, all_models: bool = False) -> list[str]:
    """Default is the base 1024px set. ``--all`` includes 2K/4K."""
    if (model or "").strip():
        return [get_model(model.strip()).id]
    if all_models:
        return [spec.id for spec in list_models()]
    return [spec.id for spec in list_models() if spec.prefetch_by_default]


def ids_needing_prefetch(requested: list[str], volume_rows: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Split requested ids into (need download, already complete)."""
    known = {row.get("model_id"): row for row in volume_rows}
    needed: list[str] = []
    cached: list[str] = []
    for model_id in requested:
        row = known.get(model_id) or {}
        # Only `complete` from the new inspector. Old deploys expose `ready`
        # when model_index.json exists, which can be a partial snapshot.
        if row.get("complete") is True:
            cached.append(model_id)
        else:
            needed.append(model_id)
    return needed, cached


def assert_model_ready(model_id: str, *, root: str | Path | None = None) -> Path:
    path = local_model_path(model_id, root=root)
    info = inspect_model_cache(model_id, root=root)
    if not info["complete"]:
        missing = ", ".join(info["missing"][:8]) or "incomplete snapshot"
        if not path.exists():
            raise FileNotFoundError(
                f"SANA weights for {model_id!r} are not on the Modal volume at {path}. "
                "Download them on CPU with `modal-sana prefetch` "
                "(or wait for the automatic CPU prefetch before generate)."
            )
        raise FileNotFoundError(
            f"SANA weights for {model_id!r} are not complete at {path} ({missing}). "
            "Download them on CPU with `modal-sana prefetch` "
            "(or wait for the automatic CPU prefetch before generate)."
        )
    return path


def download_model_weights(
    model_id: str,
    *,
    token: str | None = None,
    root: str | Path | None = None,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """Fetch one Hugging Face snapshot onto the volume. CPU-only; no torch.

    Complete snapshots are left untouched. Partial folders resume missing files.
    """
    spec = get_model(model_id)
    dest = local_model_path(model_id, root=root)
    dest.mkdir(parents=True, exist_ok=True)
    info = inspect_model_cache(model_id, root=root)
    if info["complete"]:
        return {
            "model_id": spec.id,
            "hf_id": spec.hf_id,
            "status": "cached",
            "path": str(dest),
            "bytes": info["bytes"],
        }
    from modal_sana.modal.fast_download import download_hf_repo
    from modal_sana.modal.secrets import hf_token

    payload = download_hf_repo(spec.hf_id, dest, token=token or hf_token(), on_progress=on_progress)
    method = payload.get("method") if isinstance(payload, dict) else payload
    final = inspect_model_cache(model_id, root=root)
    if not final["complete"]:
        missing = ", ".join(final["missing"][:8]) or "unknown"
        raise RuntimeError(f"Downloaded {spec.hf_id} to {dest} but snapshot is incomplete: {missing}")
    return {
        "model_id": spec.id,
        "hf_id": spec.hf_id,
        "status": "downloaded",
        "path": str(dest),
        "method": method,
        "bytes": final["bytes"],
    }


def list_ready_models(*, root: str | Path | None = None) -> list[dict[str, Any]]:
    return [inspect_model_cache(spec.id, root=root) for spec in list_models()]


def _missing_snapshot_parts(dest: Path) -> list[str]:
    if not dest.exists():
        return ["model_index.json"]
    missing: list[str] = []
    index_path = dest / "model_index.json"
    if not index_path.is_file():
        return ["model_index.json"]
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["model_index.json:invalid"]
    if not isinstance(data, dict):
        return ["model_index.json:invalid"]

    for path in dest.rglob("*"):
        if path.is_file() and path.name.endswith(INCOMPLETE_SUFFIXES):
            missing.append(f"incomplete:{path.relative_to(dest)}")

    components = [
        key
        for key, value in data.items()
        if not str(key).startswith("_") and isinstance(value, (dict, list))
    ]
    if not components:
        missing.append("model_index.json:no-components")

    for name in components:
        folder = dest / name
        if not folder.exists():
            missing.append(name)
            continue
        if name in WEIGHT_COMPONENTS:
            weights = [
                item
                for item in folder.rglob("*")
                if item.is_file() and item.suffix in WEIGHT_SUFFIXES and item.stat().st_size > 0
            ]
            if not weights:
                missing.append(f"{name}/weights")

    for index_file in dest.rglob("*.index.json"):
        try:
            payload = json.loads(index_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            missing.append(str(index_file.relative_to(dest)))
            continue
        for shard in set((payload.get("weight_map") or {}).values()):
            shard_path = index_file.parent / str(shard)
            if not shard_path.is_file() or shard_path.stat().st_size <= 0:
                missing.append(str(Path(index_file.parent.name) / str(shard)))
    return missing


def _dir_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.name.endswith(INCOMPLETE_SUFFIXES):
            total += item.stat().st_size
    return total
