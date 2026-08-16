from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

from modal_sana.core.doctor import modal_workspace
from modal_sana.core.generator import GenerateRequest, GenerateResult
from modal_sana.modal.links import app_run_url


class ModalSanaGenerator:
    """Thin adapter. Core never talks to Modal APIs directly."""

    def __init__(self) -> None:
        self.last_meta: dict[str, Any] = {}

    def generate_batches(
        self,
        batches: list[list[GenerateRequest]],
        *,
        gpu: str,
        workers: int,
        model: str,
        retry: int = 2,
        deployed: bool = False,
    ) -> Iterator[GenerateResult]:
        import modal

        from modal_sana.modal.app import app
        from modal_sana.modal.gpu import get_gpu
        from modal_sana.modal.worker import SCALEDOWN_SECONDS, SanaWorker

        spec = get_gpu(gpu)
        payloads = [{"items": [item.model_dump() for item in batch]} for batch in batches]
        if not payloads:
            return

        from modal_sana.modal.secrets import modal_download_secrets

        secrets = modal_download_secrets()

        options: dict[str, Any] = {
            "gpu": spec.modal_name,
            "max_containers": max(workers, 1),
            "scaledown_window": SCALEDOWN_SECONDS,
            "retries": modal.Retries(max_retries=max(retry, 0), backoff_coefficient=2.0),
        }
        if secrets:
            options["secrets"] = secrets

        use_deployed = deployed or os.environ.get("MODAL_SANA_DEPLOYED") == "1"
        started = time.perf_counter()
        if use_deployed:
            prefetch_meta = _prefetch_on_cpu(model, secrets, deployed=True)
            cls = modal.Cls.from_name(
                os.environ.get("MODAL_SANA_APP_NAME", "modal-sana"),
                "SanaWorker",
            )
            worker = cls.with_options(**options)(model_id=model)
            yield from _iter_results(worker, payloads, meta=self.last_meta)
            self.last_meta.update(
                {
                    "deployed": True,
                    "app_name": os.environ.get("MODAL_SANA_APP_NAME", "modal-sana"),
                    "map_wall_ms": (time.perf_counter() - started) * 1000,
                    "gpu": gpu,
                    "model": model,
                    "prefetch": prefetch_meta,
                }
            )
            return

        with modal.enable_output():
            with app.run():
                self.last_meta["modal_app_id"] = app.app_id
                self.last_meta["modal_run_url"] = app_run_url(app.app_id, modal_workspace())
                prefetch_meta = _prefetch_on_cpu(model, secrets, deployed=False)
                self.last_meta["prefetch"] = prefetch_meta
                worker = SanaWorker.with_options(**options)(model_id=model)
                yield from _iter_results(worker, payloads, meta=self.last_meta)
        self.last_meta.update(
            {
                "deployed": False,
                "map_wall_ms": (time.perf_counter() - started) * 1000,
                "gpu": gpu,
                "model": model,
            }
        )


def _prefetch_on_cpu(model: str, secrets: list[Any], *, deployed: bool) -> dict[str, Any]:
    """CPU container writes weights to the Volume. GPU never hits the network."""
    import modal

    from modal_sana.modal.prefetch import prefetch_model

    if deployed:
        fn = modal.Function.from_name(
            os.environ.get("MODAL_SANA_APP_NAME", "modal-sana"),
            "prefetch_model",
        )
        return fn.remote(model)
    if secrets:
        return prefetch_model.with_options(secrets=secrets).remote(model)
    return prefetch_model.remote(model)


def _iter_results(
    worker: Any,
    payloads: list[dict[str, Any]],
    *,
    meta: dict[str, Any],
) -> Iterator[GenerateResult]:
    for batch_result in worker.generate_batch.map(
        payloads,
        order_outputs=False,
        return_exceptions=True,
    ):
        if isinstance(batch_result, Exception):
            yield GenerateResult(
                generation_id="",
                error=str(batch_result),
                telemetry={"modal_app_id": meta.get("modal_app_id")},
            )
            continue
        batch_ids = {
            "modal_function_call_id": batch_result.get("modal_function_call_id"),
            "modal_input_id": batch_result.get("modal_input_id"),
            "container": batch_result.get("container") or {},
            "modal_app_id": meta.get("modal_app_id"),
        }
        for item in batch_result.get("items", []):
            telemetry = {
                **batch_ids,
                "load_ms": item.get("load_ms"),
                "infer_ms": item.get("infer_ms"),
                "encode_ms": item.get("encode_ms"),
                "gpu_seconds": item.get("gpu_seconds"),
                "cold_start": bool(item.get("cold_start")),
                "vram_allocated_mb": item.get("vram_allocated_mb"),
                "modal_function_call_id": item.get("modal_function_call_id")
                or batch_ids["modal_function_call_id"],
                "modal_input_id": item.get("modal_input_id") or batch_ids["modal_input_id"],
                "container": item.get("container") or batch_ids["container"],
            }
            yield GenerateResult(
                generation_id=item["generation_id"],
                image_bytes=item.get("image_bytes"),
                width=item.get("width") or 0,
                height=item.get("height") or 0,
                latency_ms=float(item.get("latency_ms") or 0),
                error=item.get("error"),
                telemetry=telemetry,
            )
