from __future__ import annotations

import json
from pathlib import Path

from rich.progress import Progress

from modal_sana.cli.prefetch import apply_prefetch_event
from modal_sana.modal.prefetch import iter_prefetch_events


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


def test_iter_events_cached_does_not_download(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "models"
    write_complete_snapshot(root / "sana-sprint-1.6b")

    def boom(*_args, **_kwargs):
        raise AssertionError("complete snapshot must not download")

    monkeypatch.setattr("modal_sana.modal.prefetch.download_model_weights", boom)
    events = list(iter_prefetch_events("sana-sprint-1.6b", root=root))
    kinds = [item["event"] for item in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "cached"
    assert events[-1]["status"] == "cached"


def test_iter_events_streams_progress(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "models"

    def fake_download(model_id, *, token=None, root=None, on_progress=None):
        if on_progress:
            on_progress(
                {
                    "status": "downloading",
                    "current": "transformer/model.safetensors",
                    "files_done": 0,
                    "files_total": 3,
                    "bytes_done": 10,
                    "bytes_total": 100,
                }
            )
            on_progress(
                {
                    "status": "complete",
                    "current": "",
                    "files_done": 3,
                    "files_total": 3,
                    "bytes_done": 100,
                    "bytes_total": 100,
                }
            )
        dest = Path(root) / model_id
        write_complete_snapshot(dest)
        return {
            "model_id": model_id,
            "status": "downloaded",
            "path": str(dest),
            "method": "files",
            "bytes": 100,
        }

    monkeypatch.setattr("modal_sana.modal.prefetch.download_model_weights", fake_download)
    events = list(iter_prefetch_events("sana-sprint-1.6b", root=root))
    kinds = [item["event"] for item in events]
    assert "start" in kinds
    assert "progress" in kinds
    assert kinds[-1] == "done"
    assert events[-1]["status"] == "downloaded"
    assert any(item.get("current") == "transformer/model.safetensors" for item in events)


def test_apply_prefetch_event_updates_bar() -> None:
    with Progress(transient=True) as progress:
        task = progress.add_task("m", total=None, model_id="sana-1.6b-4k", detail="")
        apply_prefetch_event(
            progress,
            task,
            {
                "event": "progress",
                "status": "downloading",
                "current": "vae/x.safetensors",
                "files_done": 1,
                "files_total": 4,
                "bytes_done": 50,
                "bytes_total": 200,
            },
        )
        task_data = progress.tasks[0]
        assert task_data.completed == 50
        assert task_data.total == 200
        apply_prefetch_event(progress, task, {"event": "cached", "status": "cached", "bytes": 9})
        assert progress.tasks[0].completed == 9
