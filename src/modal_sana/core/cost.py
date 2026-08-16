from __future__ import annotations

from modal_sana.modal.gpu import estimate_cost_usd, get_gpu


def item_gpu_seconds(
    *,
    load_ms: float,
    infer_ms: float,
    encode_ms: float,
    cold_start: bool,
    group_size: int,
    load_group_size: int | None = None,
) -> float:
    """GPU-seconds attributed to one image in a batched forward.

    Load time is charged only on the first ``generate_batch`` of a container,
    then split across every item in that call. Infer is split across the
    CUDA group; encode is per image.
    """
    n_infer = max(int(group_size), 1)
    n_load = max(int(load_group_size or group_size), 1)
    load_share = (max(load_ms, 0.0) / n_load) if cold_start else 0.0
    infer_share = max(infer_ms, 0.0) / n_infer
    return max((load_share + infer_share + max(encode_ms, 0.0)) / 1000.0, 0.0)


def cost_for_seconds(gpu_id: str, seconds: float) -> float:
    return estimate_cost_usd(gpu_id, seconds)


def format_usd(amount: float | None) -> str:
    if amount is None:
        return "—"
    cents = amount * 100.0
    if amount < 0.01:
        return f"${amount:.6f} ({cents:.3f}¢)"
    return f"${amount:.4f} ({cents:.2f}¢)"


def gpu_rate(gpu_id: str) -> float:
    return get_gpu(gpu_id).usd_per_second
