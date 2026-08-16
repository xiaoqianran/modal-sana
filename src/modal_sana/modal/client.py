from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from modal_sana.core.generator import GenerateRequest, GenerateResult


class ModalSanaGenerator:
    """Thin adapter. Core never talks to Modal APIs directly."""

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
        from modal_sana.modal.worker import SanaWorker

        spec = get_gpu(gpu)
        payloads = [{"items": [item.model_dump() for item in batch]} for batch in batches]
        if not payloads:
            return

        secrets = []
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if token:
            secrets.append(modal.Secret.from_dict({"HF_TOKEN": token, "HUGGING_FACE_HUB_TOKEN": token}))

        options: dict[str, Any] = {
            "gpu": spec.modal_name,
            "max_containers": max(workers, 1),
            "retries": modal.Retries(max_retries=max(retry, 0), backoff_coefficient=2.0),
        }
        if secrets:
            options["secrets"] = secrets

        use_deployed = deployed or os.environ.get("MODAL_SANA_DEPLOYED") == "1"
        if use_deployed:
            cls = modal.Cls.from_name(
                os.environ.get("MODAL_SANA_APP_NAME", "modal-sana"),
                "SanaWorker",
            )
            worker = cls.with_options(**options)(model_id=model)
            yield from _iter_results(worker, payloads)
            return

        with modal.enable_output():
            with app.run():
                worker = SanaWorker.with_options(**options)(model_id=model)
                yield from _iter_results(worker, payloads)


def _iter_results(worker: Any, payloads: list[dict[str, Any]]) -> Iterator[GenerateResult]:
    for batch_result in worker.generate_batch.map(
        payloads,
        order_outputs=False,
        return_exceptions=True,
    ):
        if isinstance(batch_result, Exception):
            yield GenerateResult(generation_id="", error=str(batch_result))
            continue
        for item in batch_result.get("items", []):
            yield GenerateResult(
                generation_id=item["generation_id"],
                image_bytes=item.get("image_bytes"),
                width=item.get("width") or 0,
                height=item.get("height") or 0,
                latency_ms=float(item.get("latency_ms") or 0),
                error=item.get("error"),
            )
