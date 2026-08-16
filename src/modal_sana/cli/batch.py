from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

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
from modal_sana.core.prompts import parse_prompt_file


def batch(
    file: Annotated[Path, typer.Argument(exists=True, readable=True, help="txt / jsonl / json / csv")],
    model: OptModel = "sana-sprint-1.6b",
    gpu: OptGPU = "L40S",
    count: OptCount = 1,
    steps: OptSteps = None,
    guidance: OptGuidance = None,
    width: OptWidth = None,
    height: OptHeight = None,
    seed: OptSeed = None,
    batch_size: OptBatch = 4,
    workers: OptWorkers = 2,
    retry: OptRetry = 3,
    image_format: OptFormat = "png",
    quality: OptQuality = 90,
    negative: OptNegative = "",
    dry_run: OptDryRun = False,
    deployed: OptDeployed = None,
    deduplicate: OptDedup = False,
) -> None:
    """Parse a prompt file and run it as one Job."""
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
    specs = parse_prompt_file(file)
    for spec in specs:
        if spec.count == 1 and count > 1 and spec.seed is None:
            spec.count = count
    job = svc.create_job(specs, config)
    run_and_watch(svc, job)
