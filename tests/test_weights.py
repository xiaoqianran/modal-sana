from __future__ import annotations

from pathlib import Path

import pytest

from modal_sana.modal.weights import (
    assert_model_ready,
    download_model_weights,
    is_model_ready,
    list_ready_models,
    local_model_path,
)
from modal_sana.models.sana.registry import list_models


def test_local_path_and_ready(tmp_path: Path) -> None:
    root = tmp_path / "models"
    path = local_model_path("sana-sprint-1.6b", root=root)
    assert path == root / "sana-sprint-1.6b"
    assert not is_model_ready("sana-sprint-1.6b", root=root)
    path.mkdir(parents=True)
    (path / "model_index.json").write_text("{}", encoding="utf-8")
    assert is_model_ready("sana-sprint-1.6b", root=root)
    assert assert_model_ready("sana-sprint-1.6b", root=root) == path


def test_assert_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not on the Modal volume"):
        assert_model_ready("sana-sprint-1.6b", root=tmp_path / "models")


def test_download_skips_when_cached(tmp_path: Path) -> None:
    root = tmp_path / "models"
    dest = local_model_path("sana-sprint-1.6b", root=root)
    dest.mkdir(parents=True)
    (dest / "model_index.json").write_text("{}", encoding="utf-8")
    result = download_model_weights("sana-sprint-1.6b", root=root)
    assert result["status"] == "cached"
    assert result["hf_id"].startswith("Efficient-Large-Model/")


def test_download_writes_snapshot(tmp_path: Path, monkeypatch) -> None:
    import sys
    import types

    root = tmp_path / "models"

    def fake_snapshot(*, repo_id: str, local_dir: str, token=None):
        dest = Path(local_dir)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "model_index.json").write_text(repo_id, encoding="utf-8")

    fake_hub = types.ModuleType("huggingface_hub")
    fake_hub.snapshot_download = fake_snapshot
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    result = download_model_weights("sana-sprint-1.6b", root=root)
    assert result["status"] == "downloaded"
    assert is_model_ready("sana-sprint-1.6b", root=root)


def test_list_ready_models(tmp_path: Path) -> None:
    root = tmp_path / "models"
    rows = list_ready_models(root=root)
    assert {row["model_id"] for row in rows} == {spec.id for spec in list_models()}
    assert all(row["ready"] is False for row in rows)


def test_worker_never_downloads_from_hf() -> None:
    text = Path("src/modal_sana/modal/worker.py").read_text(encoding="utf-8")
    assert "local_files_only=True" in text
    assert "prefetch_model" in text
    assert "from_pretrained(spec.hf_id" not in text
