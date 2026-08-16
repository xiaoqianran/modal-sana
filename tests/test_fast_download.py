from __future__ import annotations

from pathlib import Path

from modal_sana.modal.fast_download import download_hf_repo


def test_skips_complete_files(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "model"
    dest.mkdir()
    (dest / "a.bin").write_bytes(b"12345")
    monkeypatch.setattr(
        "modal_sana.modal.fast_download.list_repo_files",
        lambda *_args, **_kwargs: [{"path": "a.bin", "size": 5}],
    )
    called: list[str] = []
    monkeypatch.setattr(
        "modal_sana.modal.fast_download._download_one",
        lambda *_args, **_kwargs: called.append("hit"),
    )
    events: list[dict] = []
    method = download_hf_repo("org/repo", dest, on_progress=events.append)
    assert method == "files"
    assert called == []
    assert any(item.get("status") == "skip" for item in events)


def test_downloads_size_mismatch(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "model"
    dest.mkdir()
    (dest / "a.bin").write_bytes(b"12")
    monkeypatch.setattr(
        "modal_sana.modal.fast_download.list_repo_files",
        lambda *_args, **_kwargs: [{"path": "a.bin", "size": 5}],
    )

    def fake_dl(_repo, rel, dest_dir, _token, on_bytes):
        path = dest_dir / rel
        path.write_bytes(b"12345")
        on_bytes(5)

    monkeypatch.setattr("modal_sana.modal.fast_download._download_one", fake_dl)
    events: list[dict] = []
    download_hf_repo("org/repo", dest, on_progress=events.append)
    assert (dest / "a.bin").read_bytes() == b"12345"
    assert any(item.get("status") == "downloading" for item in events)
    assert events[-1]["status"] == "complete"
    assert events[-1]["files_done"] == 1


def test_fallback_when_listing_empty(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "model"
    monkeypatch.setattr("modal_sana.modal.fast_download.list_repo_files", lambda *_a, **_k: [])
    monkeypatch.setattr("modal_sana.modal.fast_download._try_hf_cli", lambda *_a, **_k: False)
    seen: list[str] = []

    def fake_snap(repo_id, dest_dir, token):
        seen.append(repo_id)
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        (Path(dest_dir) / "model_index.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr("modal_sana.modal.fast_download._snapshot_download", fake_snap)
    method = download_hf_repo("org/repo", dest)
    assert method == "snapshot_download"
    assert seen == ["org/repo"]
