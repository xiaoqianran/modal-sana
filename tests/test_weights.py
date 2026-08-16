from __future__ import annotations

import json
from pathlib import Path

import pytest

from modal_sana.modal.weights import (
    assert_model_ready,
    download_model_weights,
    ids_needing_prefetch,
    inspect_model_cache,
    is_model_ready,
    list_ready_models,
    local_model_path,
    models_to_prefetch,
)
from modal_sana.models.sana.registry import list_models


def write_complete_snapshot(dest: Path, *, hf_id: str = "org/model") -> None:
    dest.mkdir(parents=True, exist_ok=True)
    index = {
        "_class_name": "SanaPipeline",
        "transformer": ["SanaTransformer2DModel"],
        "vae": ["AutoencoderDC"],
        "text_encoder": ["Gemma2Model"],
        "scheduler": ["FlowMatchEulerDiscreteScheduler"],
    }
    (dest / "model_index.json").write_text(json.dumps(index), encoding="utf-8")
    for name in ("transformer", "vae", "text_encoder"):
        folder = dest / name
        folder.mkdir(exist_ok=True)
        (folder / "diffusion_pytorch_model.safetensors").write_bytes(b"fake-weights")
        (folder / "config.json").write_text("{}", encoding="utf-8")
    (dest / "scheduler").mkdir(exist_ok=True)
    (dest / "scheduler" / "scheduler_config.json").write_text(json.dumps({"hf": hf_id}), encoding="utf-8")


def test_local_path_and_ready(tmp_path: Path) -> None:
    root = tmp_path / "models"
    path = local_model_path("sana-sprint-1.6b", root=root)
    assert path == root / "sana-sprint-1.6b"
    assert not is_model_ready("sana-sprint-1.6b", root=root)
    path.mkdir(parents=True)
    (path / "model_index.json").write_text("{}", encoding="utf-8")
    assert not is_model_ready("sana-sprint-1.6b", root=root)
    write_complete_snapshot(path)
    assert is_model_ready("sana-sprint-1.6b", root=root)
    assert assert_model_ready("sana-sprint-1.6b", root=root) == path


def test_index_only_is_incomplete(tmp_path: Path) -> None:
    dest = local_model_path("sana-sprint-1.6b", root=tmp_path / "models")
    dest.mkdir(parents=True)
    (dest / "model_index.json").write_text("{}", encoding="utf-8")
    info = inspect_model_cache("sana-sprint-1.6b", root=tmp_path / "models")
    assert info["complete"] is False
    assert "model_index.json:no-components" in info["missing"]


def test_aria2_leftover_is_incomplete(tmp_path: Path) -> None:
    dest = local_model_path("sana-sprint-1.6b", root=tmp_path / "models")
    write_complete_snapshot(dest)
    (dest / "transformer" / "diffusion_pytorch_model.safetensors.aria2").write_text("x")
    assert not is_model_ready("sana-sprint-1.6b", root=tmp_path / "models")


def test_missing_shard_is_incomplete(tmp_path: Path) -> None:
    dest = local_model_path("sana-sprint-1.6b", root=tmp_path / "models")
    write_complete_snapshot(dest)
    payload = {"weight_map": {"a": "model-00001-of-00002.safetensors"}}
    (dest / "transformer" / "diffusion_pytorch_model.safetensors.index.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    info = inspect_model_cache("sana-sprint-1.6b", root=tmp_path / "models")
    assert info["complete"] is False
    assert any("model-00001-of-00002.safetensors" in item for item in info["missing"])


def test_assert_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not on the Modal volume"):
        assert_model_ready("sana-sprint-1.6b", root=tmp_path / "models")


def test_download_skips_when_cached(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "models"
    dest = local_model_path("sana-sprint-1.6b", root=root)
    write_complete_snapshot(dest)

    def boom(*_args, **_kwargs):
        raise AssertionError("complete snapshot must not hit the network")

    monkeypatch.setattr("modal_sana.modal.fast_download.download_hf_repo", boom)
    result = download_model_weights("sana-sprint-1.6b", root=root)
    assert result["status"] == "cached"
    assert result["hf_id"].startswith("Efficient-Large-Model/")
    assert result["bytes"] > 0


def test_download_writes_snapshot(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "models"

    def fake_fetch(repo_id: str, dest, token=None, *, on_progress=None):
        dest = Path(dest)
        write_complete_snapshot(dest, hf_id=repo_id)
        if on_progress:
            on_progress(
                {
                    "status": "complete",
                    "current": "",
                    "files_done": 1,
                    "files_total": 1,
                    "bytes_done": 12,
                    "bytes_total": 12,
                }
            )
        return "aria2c"

    monkeypatch.setattr("modal_sana.modal.fast_download.download_hf_repo", fake_fetch)
    result = download_model_weights("sana-sprint-1.6b", root=root)
    assert result["status"] == "downloaded"
    assert result["method"] == "aria2c"
    assert is_model_ready("sana-sprint-1.6b", root=root)


def test_ids_needing_prefetch_requires_complete_flag() -> None:
    needed, cached = ids_needing_prefetch(
        ["sana-sprint-1.6b", "sana-1.6b-2k", "sana-1.6b-4k"],
        [
            {"model_id": "sana-sprint-1.6b", "complete": True, "ready": True},
            {"model_id": "sana-1.6b-2k", "ready": True},
            {"model_id": "sana-1.6b-4k", "complete": False, "ready": False},
        ],
    )
    assert cached == ["sana-sprint-1.6b"]
    assert needed == ["sana-1.6b-2k", "sana-1.6b-4k"]
    only_ready, _ = ids_needing_prefetch(
        ["sana-sprint-1.6b"],
        [{"model_id": "sana-sprint-1.6b", "ready": True}],
    )
    assert only_ready == ["sana-sprint-1.6b"]


def test_prefetch_default_is_base_models() -> None:
    ids = models_to_prefetch(None)
    assert "sana-sprint-1.6b" in ids
    assert "sana-1.5-4.8b" in ids
    assert "sana-1.6b-2k" not in ids
    assert "sana-1.6b-4k" not in ids
    assert len(ids) >= 5
    all_ids = models_to_prefetch(None, all_models=True)
    assert all_ids == [spec.id for spec in list_models()]
    assert "sana-1.6b-2k" in all_ids
    assert "sana-1.6b-4k" in all_ids
    assert models_to_prefetch("sana-sprint-0.6b") == ["sana-sprint-0.6b"]


def test_list_ready_models(tmp_path: Path) -> None:
    root = tmp_path / "models"
    rows = list_ready_models(root=root)
    assert {row["model_id"] for row in rows} == {spec.id for spec in list_models()}
    assert all(row["ready"] is False for row in rows)
    assert all(row["complete"] is False for row in rows)


def test_worker_never_downloads_from_hf() -> None:
    text = Path("src/modal_sana/modal/worker.py").read_text(encoding="utf-8")
    assert "local_files_only=True" in text
    assert "prefetch_model" in text
    assert "prefetch_progress" in text
    assert "from_pretrained(spec.hf_id" not in text
