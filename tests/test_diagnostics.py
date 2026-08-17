from modal_sana.core.diagnostics import diagnose_cost_ledger


def test_diagnose_cost_breaks_out_encode_batch_and_pipeline_stages() -> None:
    ledger = {
        "job_id": "job_x",
        "total": 3,
        "items": [
            {
                "kind": "gpu_load",
                "load_ms": 1000,
                "gpu_seconds": 1.0,
                "cost_usd": 0.000542,
                "usd_per_second": 0.000542,
            },
            {
                "kind": "gpu_generate",
                "infer_ms": 500,
                "encode_ms": 250,
                "text_encode_ms": 75,
                "transformer_ms": 300,
                "vae_decode_ms": 75,
                "postprocess_ms": 25,
                "pipeline_other_ms": 25,
                "gpu_seconds": 0.75,
                "cost_usd": 0.0004065,
                "usd_per_second": 0.000542,
                "batch_size_requested": 8,
                "batch_size_effective": 4,
                "vram_peak_mb": 24000,
                "vram_oom_peak_mb": 47000,
            },
            {
                "kind": "gpu_generate",
                "infer_ms": 500,
                "encode_ms": 250,
                "text_encode_ms": 75,
                "transformer_ms": 300,
                "vae_decode_ms": 75,
                "postprocess_ms": 25,
                "pipeline_other_ms": 25,
                "gpu_seconds": 0.75,
                "cost_usd": 0.0004065,
                "usd_per_second": 0.000542,
                "batch_size_requested": 8,
                "batch_size_effective": 4,
                "vram_peak_mb": 24000,
                "vram_oom_peak_mb": 47000,
            },
        ],
        "summary": {},
    }
    out = diagnose_cost_ledger(ledger)
    assert out["images"] == 2
    assert out["batch"]["avg_effective"] == 4
    assert out["batch"]["fallback_events"] == 2
    assert out["phases"]["encode_in_gpu_container"]["ms"] == 500
    stages = out["phases"]["inference"]["stages"]
    assert stages["transformer"]["ms"] == 600
    assert stages["transformer"]["share_of_inference"] == 0.6
    assert out["memory"]["oom_attempt_peak_gb"] > 45
    assert any(item["code"] == "batch_fallback" for item in out["findings"])
    assert any(item["code"] == "pipeline_stage_hotspot" for item in out["findings"])
