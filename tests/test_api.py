from __future__ import annotations

from fastapi.testclient import TestClient

from modal_sana.core.config import Settings
from modal_sana.web import api
from modal_sana.web.server import app


def test_create_job_and_gallery(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DATA_DIR", str(tmp_path / "data"))
    api.configure(Settings(data_dir=tmp_path / "data"))
    client = TestClient(app)
    created = client.post(
        "/api/jobs",
        json={"prompt": "a bicycle in the rain", "count": 2, "seed": 3, "dry_run": True},
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["id"]
    # The API starts a background thread; wait for completion.
    for _ in range(50):
        detail = client.get(f"/api/jobs/{job_id}")
        status = detail.json()["job"]["status"]
        if status in {"completed", "failed"}:
            break
        import time

        time.sleep(0.05)
    assert detail.json()["job"]["status"] == "completed"
    gallery = client.get("/api/gallery", params={"job_id": job_id})
    assert gallery.status_code == 200
    assert gallery.json()["total"] == 2
    image_id = gallery.json()["items"][0]["id"]
    file = client.get(f"/api/images/{image_id}/file")
    assert file.status_code == 200
    assert file.content[:4] in {b"RIFF", b"\x89PNG"}


def test_meta_and_health() -> None:
    client = TestClient(app)
    assert client.get("/api/health").json()["status"] == "ok"
    meta = client.get("/api/meta").json()
    assert any(model["id"] == "sana-sprint-1.6b" for model in meta["models"])
    assert any(gpu["id"] == "L40S" for gpu in meta["gpus"])
