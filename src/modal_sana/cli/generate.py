from __future__ import annotations

from modal_sana.cli.common import (
    OptBatch,
    OptCount,
    OptDedup,
    OptDeployed,
    OptDryRun,
    OptFormat,
    OptGPU,
    OptGuidance,
    OptHeight,
    OptModel,
    OptNegative,
    OptPrompt,
    OptQuality,
    OptRetry,
    OptSeed,
    OptSteps,
    OptWidth,
    OptWorkers,
    job_config,
    run_and_watch,
    service,
)
from modal_sana.schemas.job import PromptSpec


def generate(
    prompt: OptPrompt = None,
    model: OptModel = "sana-sprint-1.6b",
    gpu: OptGPU = "L40S",
    count: OptCount = 1,
    steps: OptSteps = None,
    guidance: OptGuidance = None,
    width: OptWidth = None,
    height: OptHeight = None,
    seed: OptSeed = None,
    batch_size: OptBatch = 4,
    workers: OptWorkers = 1,
    retry: OptRetry = 3,
    image_format: OptFormat = "png",
    quality: OptQuality = 90,
    negative: OptNegative = "",
    dry_run: OptDryRun = False,
    deployed: OptDeployed = None,
    deduplicate: OptDedup = False,
) -> None:
    """Generate one prompt. Use --count to expand seeds."""
    if not prompt:
        raise SystemExit("Pass a prompt, e.g. modal-sana generate \"a white cat\"")
    svc = service()
    config = job_config(
        model=model,
        gpu=gpu,
        count=count,
        steps=steps,
        guidance=guidance,
        width=width,
        height=height,
        seed=seed,
        batch_size=batch_size,
        workers=workers,
        retry=retry,
        image_format=image_format,
        quality=quality,
        negative=negative,
        dry_run=dry_run,
        deployed=deployed,
        deduplicate=deduplicate,
    )
    job = svc.create_job([PromptSpec(prompt=prompt, count=count, seed=seed)], config)
    run_and_watch(svc, job)
