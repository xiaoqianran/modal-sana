from __future__ import annotations

from typing import Any

_STAGE_LABELS = {
    "text_encode_ms": "Gemma 文本编码",
    "transformer_ms": "SANA Transformer",
    "vae_decode_ms": "VAE 解码",
    "postprocess_ms": "图像后处理",
    "pipeline_other_ms": "Pipeline 其他开销",
}


def diagnose_cost_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    """Turn raw Modal cost events into an actionable per-job performance report.

    The worker emits one gpu_generate event per image. infer_ms and fine-grained
    pipeline timings are per-image shares of a batched forward; encode_ms is
    per-image CPU compression that still happens inside the billed GPU container.
    """
    items = list(ledger.get("items") or [])
    generated = [item for item in items if item.get("kind") == "gpu_generate"]
    loads = [item for item in items if item.get("kind") == "gpu_load"]

    def f(item: dict[str, Any], key: str) -> float:
        try:
            return float(item.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def phase_cost(rows: list[dict[str, Any]], ms_key: str) -> float:
        return sum((f(row, ms_key) / 1000.0) * f(row, "usd_per_second") for row in rows)

    images = len(generated)
    infer_ms = sum(f(row, "infer_ms") for row in generated)
    encode_ms = sum(f(row, "encode_ms") for row in generated)
    load_ms = sum(f(row, "load_ms") for row in loads)
    infer_cost = phase_cost(generated, "infer_ms")
    encode_cost = phase_cost(generated, "encode_ms")
    load_cost = sum(f(row, "cost_usd") for row in loads)
    total_cost = sum(f(row, "cost_usd") for row in items)
    gpu_seconds = sum(f(row, "gpu_seconds") for row in items)

    pipeline_stages: dict[str, dict[str, Any]] = {}
    for key, label in _STAGE_LABELS.items():
        stage_ms = sum(f(row, key) for row in generated)
        if stage_ms <= 0:
            continue
        stage_cost = phase_cost(generated, key)
        pipeline_stages[key.removesuffix("_ms")] = {
            "label": label,
            "ms": stage_ms,
            "ms_per_image": stage_ms / images if images else None,
            "cost_usd": stage_cost,
            "share_of_inference": stage_ms / infer_ms if infer_ms else 0.0,
            "share_of_total_cost": stage_cost / total_cost if total_cost else 0.0,
        }

    requested = [
        int(row.get("batch_size_requested") or 0)
        for row in generated
        if row.get("batch_size_requested")
    ]
    effective = [
        int(row.get("batch_size_effective") or 0)
        for row in generated
        if row.get("batch_size_effective")
    ]
    fallback = [
        row
        for row in generated
        if (
            row.get("batch_size_requested")
            and row.get("batch_size_effective")
            and int(row["batch_size_effective"]) < int(row["batch_size_requested"])
        )
        or f(row, "vram_oom_peak_mb") > 0
    ]
    peak_mb = max((f(row, "vram_peak_mb") for row in generated), default=0.0)
    oom_peak_mb = max((f(row, "vram_oom_peak_mb") for row in generated), default=0.0)

    accounted = load_cost + infer_cost + encode_cost
    remainder = max(total_cost - accounted, 0.0)
    encode_share = (encode_cost / total_cost) if total_cost else 0.0
    load_share = (load_cost / total_cost) if total_cost else 0.0
    fallback_share = (len(fallback) / images) if images else 0.0

    findings: list[dict[str, Any]] = []
    if encode_share >= 0.15:
        findings.append(
            {
                "severity": "high" if encode_share >= 0.30 else "medium",
                "code": "encoding_hotspot",
                "title": "图片压缩正在消耗明显的 GPU 计费时间",
                "detail": f"编码约占本任务估算费用的 {encode_share * 100:.1f}% 。",
            }
        )
    if fallback:
        findings.append(
            {
                "severity": "high",
                "code": "batch_fallback",
                "title": "请求 batch 没有稳定跑满",
                "detail": f"{len(fallback)}/{images} 个生成事件出现 requested > effective 或 OOM 峰值。",
            }
        )
    if load_share >= 0.20:
        findings.append(
            {
                "severity": "medium",
                "code": "cold_start",
                "title": "冷启动/模型搬运占比偏高",
                "detail": f"加载约占本任务估算费用的 {load_share * 100:.1f}% 。",
            }
        )
    if images and effective and sum(effective) / len(effective) <= 4:
        findings.append(
            {
                "severity": "medium",
                "code": "small_effective_batch",
                "title": "L40S 的有效 batch 偏小",
                "detail": f"平均 effective batch 为 {sum(effective) / len(effective):.2f}。",
            }
        )
    if pipeline_stages:
        hotspot = max(pipeline_stages.values(), key=lambda item: float(item["share_of_inference"]))
        if float(hotspot["share_of_inference"]) >= 0.40:
            findings.append(
                {
                    "severity": "medium",
                    "code": "pipeline_stage_hotspot",
                    "title": f"推理主要慢在{hotspot['label']}",
                    "detail": f"约占 infer_ms 的 {float(hotspot['share_of_inference']) * 100:.1f}% 。",
                }
            )
    if not findings:
        findings.append(
            {
                "severity": "info",
                "code": "no_single_hotspot",
                "title": "没有单一异常项占据主要成本",
                "detail": "继续比较阶段耗时、有效 batch 与显存峰值。",
            }
        )

    return {
        "job_id": ledger.get("job_id"),
        "events": len(items),
        "images": images,
        "truncated": int(ledger.get("total") or 0) > len(items),
        "gpu_seconds": gpu_seconds,
        "gpu_seconds_per_image": gpu_seconds / images if images else None,
        "total_cost_usd": total_cost,
        "cost_per_image_usd": total_cost / images if images else None,
        "cost_per_100_images_usd": total_cost * 100 / images if images else None,
        "phases": {
            "load": {"ms": load_ms, "cost_usd": load_cost, "share": load_share},
            "inference": {
                "ms": infer_ms,
                "ms_per_image": infer_ms / images if images else None,
                "cost_usd": infer_cost,
                "share": infer_cost / total_cost if total_cost else 0.0,
                "stages": pipeline_stages,
            },
            "encode_in_gpu_container": {
                "ms": encode_ms,
                "ms_per_image": encode_ms / images if images else None,
                "cost_usd": encode_cost,
                "share": encode_share,
            },
            "other_or_rounding": {
                "cost_usd": remainder,
                "share": remainder / total_cost if total_cost else 0.0,
            },
        },
        "batch": {
            "avg_requested": sum(requested) / len(requested) if requested else None,
            "avg_effective": sum(effective) / len(effective) if effective else None,
            "fallback_events": len(fallback),
            "fallback_share": fallback_share,
        },
        "memory": {
            "peak_gb": peak_mb / 1024.0 if peak_mb else None,
            "oom_attempt_peak_gb": oom_peak_mb / 1024.0 if oom_peak_mb else None,
        },
        "findings": findings,
        "ledger_summary": ledger.get("summary") or {},
    }
