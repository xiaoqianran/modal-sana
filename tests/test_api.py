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
    assert created.json()["config"]["deployed"] is None
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
    assert file.content[:4] == b"\x89PNG"
    cost = client.get(f"/api/jobs/{job_id}/cost")
    assert cost.status_code == 200
    assert cost.json()["cost_usd"] == 0
    trace = client.get(f"/api/jobs/{job_id}/trace")
    assert trace.status_code == 200
    names = {span["name"] for span in trace.json()["spans"]}
    assert "job.run" in names
    assert "modal.generate" in names


def test_meta_and_health(monkeypatch) -> None:
    monkeypatch.setattr(
        "modal_sana.web.api.inspect_deploy_target",
        lambda: {
            "app_name": "modal-sana",
            "available": True,
            "error": None,
            "preference": "auto",
            "would_use": "deployed",
            "snapshots": True,
            "note": "found deployed app",
            "deploy_command": "uv run modal deploy -m modal_sana.modal.worker",
            "not_modal_serve": True,
        },
    )
    client = TestClient(app)
    assert client.get("/api/health").json()["status"] == "ok"
    meta = client.get("/api/meta").json()
    assert any(model["id"] == "sana-sprint-1.6b" for model in meta["models"])
    sprint = next(model for model in meta["models"] if model["id"] == "sana-sprint-1.6b")
    assert sprint["native_width"] == 1024
    fourk = next(model for model in meta["models"] if model["id"] == "sana-1.6b-4k")
    assert fourk["native_width"] == 4096
    assert fourk["vae_tiling"] is True
    assert fourk["recommended_gpu"] == "RTX-PRO-6000"
    assert any(gpu["id"] == "L40S" for gpu in meta["gpus"])
    assert any(gpu["id"] == "H100" and gpu["usd_per_second"] > 0 for gpu in meta["gpus"])
    assert meta["runtime"]["would_use"] == "deployed"
    assert meta["runtime"]["not_modal_serve"] is True
    assert meta["defaults"]["prefer_deployed"] is True
    assert meta["defaults"]["image_format"] == "png"
    assert meta["version"]


def test_forecast_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MODAL_SANA_DATA_DIR", str(tmp_path / "data"))
    api.configure(Settings(data_dir=tmp_path / "data"))
    monkeypatch.setattr("modal_sana.web.api.load_dict_events", lambda refresh=False: [])
    monkeypatch.setattr(
        "modal_sana.web.api.safe_query_shared_ledger",
        lambda **kwargs: {
            "items": [],
            "total": 0,
            "page": 1,
            "per_page": 20,
            "pages": 1,
            "summary": {"total_cost_usd": 0, "load_cost_usd": 0, "generate_cost_usd": 0},
            "snapshots": {},
            "periods": [],
            "source": {},
        },
    )
    monkeypatch.setattr(
        "modal_sana.web.api.workspace_balance",
        lambda: {
            "ok": True,
            "remaining_usd": 27.36,
            "metered_usd": 2.64,
            "billed_usd": 0.0,
            "credits_applied_usd": 2.64,
            "notes": "test",
        },
    )
    client = TestClient(app)
    response = client.get(
        "/api/cost/forecast",
        params={"model": "sana-1.5-4.8b", "gpu": "H100", "count": 2, "width": 1024, "height": 1024},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["predict"]["gpu"] == "H100"
    assert body["predict"]["model"] == "sana-1.5-4.8b"
    assert body["predict"]["load"]["usd"] > 0
    assert body["predict"]["generate"]["usd"] > 0
    assert body["balance"]["remaining_usd"] == 27.36
    bad = client.get("/api/cost/forecast", params={"gpu": "not-a-gpu"})
    assert bad.status_code == 400
