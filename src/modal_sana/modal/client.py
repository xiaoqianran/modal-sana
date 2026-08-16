from __future__ import annotations

import os
import time
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from modal_sana.core.doctor import modal_workspace
from modal_sana.core.generator import GenerateRequest, GenerateResult
from modal_sana.modal.links import app_run_url


def ensure_local_app_objects() -> None:
    """Register every App function/class before ``app.run()`` hydrates.

    Importing only ``modal.ledger`` (the web cost API) registers
    ``archive_cost_events`` / ``list_cost_events``. An ephemeral run then
    hydrates just those two; ``prefetch_model.remote()`` raises
    ``Function has not been hydrated``.
    """
    from modal_sana.modal.ledger import archive_cost_events, list_cost_events
    from modal_sana.modal.prefetch import list_volume_models, prefetch_model, registered_model_ids
    from modal_sana.modal.worker import SanaWorker

    _ = (
        SanaWorker,
        prefetch_model,
        list_volume_models,
        registered_model_ids,
        archive_cost_events,
        list_cost_events,
    )


def build_worker_options(
    *,
    gpu: str,
    workers: int,
    retry: int,
    model: str,
    secrets: list[Any] | None = None,
) -> dict[str, Any]:
    """Runtime GPU/model bind. Class decorator ``gpu="L40S"`` is only a fallback."""
    import modal

    from modal_sana.modal.gpu import get_gpu
    from modal_sana.modal.worker import SCALEDOWN_SECONDS

    spec = get_gpu(gpu)
    options: dict[str, Any] = {
        "gpu": spec.modal_name,
        "max_containers": max(workers, 1),
        "scaledown_window": SCALEDOWN_SECONDS,
        "retries": modal.Retries(max_retries=max(retry, 0), backoff_coefficient=2.0),
        "env": {
            "MODAL_SANA_REQUESTED_GPU": spec.id,
            "MODAL_SANA_REQUESTED_MODEL": model,
        },
    }
    if secrets:
        options["secrets"] = secrets
    return options


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
        deployed: bool | None = None,
    ) -> Iterator[GenerateResult]:
        import modal

        from modal_sana.modal.app import app
        from modal_sana.modal.deploy_mode import ensure_deployed_or_fallback
        from modal_sana.modal.gpu import get_gpu
        from modal_sana.modal.secrets import modal_download_secrets

        ensure_local_app_objects()
        spec = get_gpu(gpu)
        payloads = [_annotate_payload(batch, gpu=spec.id, model=model) for batch in batches]
        if not payloads:
            return

        secrets = modal_download_secrets()
        needed = [model]
        for batch in batches:
            for request in batch:
                if request.model and request.model not in needed:
                    needed.append(request.model)
        decision = ensure_deployed_or_fallback(deployed, required_models=needed)
        use_deployed = decision.use_deployed
        print(
            f"modal-sana: Modal path={decision.mode} ({decision.reason}) "
            f"app={decision.app_name}",
            flush=True,
        )
        started = time.perf_counter()
        self.last_meta.update(
            {
                "requested_gpu": spec.id,
                "modal_gpu": spec.modal_name,
                "model": model,
                "prefetch_by_model": {},
                **decision.as_meta(),
            }
        )
        if use_deployed:
            from modal.exception import NotFoundError

            from modal_sana.modal.deploy_mode import DeployedAppMissing, missing_app_message

            try:
                yield from _dispatch(
                    payloads,
                    gpu=spec.id,
                    workers=workers,
                    retry=retry,
                    default_model=model,
                    secrets=secrets,
                    deployed=True,
                    meta=self.last_meta,
                )
            except NotFoundError as exc:
                raise DeployedAppMissing(missing_app_message(decision.app_name, str(exc))) from exc
            self.last_meta.update(
                {
                    "deployed": True,
                    "app_name": os.environ.get("MODAL_SANA_APP_NAME", "modal-sana"),
                    "map_wall_ms": (time.perf_counter() - started) * 1000,
                    "gpu": gpu,
                }
            )
            return

        with modal.enable_output():
            with app.run():
                self.last_meta["modal_app_id"] = app.app_id
                self.last_meta["modal_run_url"] = app_run_url(app.app_id, modal_workspace())
                yield from _dispatch(
                    payloads,
                    gpu=spec.id,
                    workers=workers,
                    retry=retry,
                    default_model=model,
                    secrets=secrets,
                    deployed=False,
                    meta=self.last_meta,
                )
        self.last_meta.update(
            {
                "deployed": False,
                "map_wall_ms": (time.perf_counter() - started) * 1000,
                "gpu": gpu,
                "model": model,
            }
        )


def _annotate_payload(batch: list[GenerateRequest], *, gpu: str, model: str) -> dict[str, Any]:
    items = []
    for item in batch:
        data = item.model_dump()
        data["requested_gpu"] = gpu
        data["model"] = data.get("model") or model
        items.append(data)
    return {"items": items, "requested_gpu": gpu, "requested_model": model}


def _dispatch(
    payloads: list[dict[str, Any]],
    *,
    gpu: str,
    workers: int,
    retry: int,
    default_model: str,
    secrets: list[Any],
    deployed: bool,
    meta: dict[str, Any],
) -> Iterator[GenerateResult]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for payload in payloads:
        items = payload.get("items") or []
        model_id = (items[0].get("model") if items else None) or default_model
        grouped[str(model_id)].append(payload)

    for model_id, group in grouped.items():
        options = build_worker_options(
            gpu=gpu,
            workers=workers,
            retry=retry,
            model=model_id,
            secrets=secrets,
        )
        prefetch_meta = _prefetch_on_cpu(model_id, secrets, deployed=deployed)
        meta["prefetch"] = prefetch_meta
        meta.setdefault("prefetch_by_model", {})[model_id] = prefetch_meta
        worker = _make_worker(model_id, options, deployed=deployed)
        yield from _iter_results(worker, group, meta=meta)


def _make_worker(model: str, options: dict[str, Any], *, deployed: bool) -> Any:
    import modal

    from modal_sana.modal.worker import SanaWorker

    if deployed:
        cls = modal.Cls.from_name(
            os.environ.get("MODAL_SANA_APP_NAME", "modal-sana"),
            "SanaWorker",
        )
        return cls.with_options(**options)(model_id=model)
    return SanaWorker.with_options(**options)(model_id=model)


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
            "runtime": batch_result.get("runtime") or {},
        }
        if batch_result.get("runtime"):
            meta["runtime"] = batch_result["runtime"]
        for item in batch_result.get("items", []):
            applied = item.get("applied") or {}
            runtime = item.get("runtime") or batch_ids["runtime"] or {}
            telemetry = {
                **batch_ids,
                "deploy_mode": meta.get("deploy_mode"),
                "deployed": meta.get("deployed"),
                "deploy_reason": meta.get("deploy_reason"),
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
                "applied": applied,
                "runtime": runtime,
                "actual_gpu": applied.get("actual_gpu") or runtime.get("actual_gpu"),
                "actual_device": applied.get("actual_device") or runtime.get("actual_device"),
                "requested_gpu": applied.get("requested_gpu") or runtime.get("requested_gpu"),
                "gpu_match": applied.get("gpu_match", runtime.get("gpu_match")),
                "loaded_model": applied.get("model") or runtime.get("loaded_model"),
                "requested_model": applied.get("requested_model") or runtime.get("requested_model"),
                "model_match": applied.get("model_match", runtime.get("model_match")),
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
