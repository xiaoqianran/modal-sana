from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path

from modal_sana.models.sana.registry import get_model
from modal_sana.schemas.job import JobConfig, PromptSpec


def parse_prompt_text(text: str) -> list[PromptSpec]:
    specs: list[PromptSpec] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        specs.append(PromptSpec(prompt=line))
    return specs


def _from_mapping(row: dict) -> PromptSpec:
    prompt = row.get("prompt") or row.get("text") or row.get("positive")
    if not prompt:
        raise ValueError(f"Row is missing a prompt field: {row!r}")
    count = int(row.get("count") or 1)
    seed = row.get("seed")
    return PromptSpec(
        prompt=str(prompt),
        negative_prompt=str(row.get("negative_prompt") or row.get("negative") or ""),
        count=max(count, 1),
        seed=int(seed) if seed not in (None, "") else None,
        width=int(row["width"]) if row.get("width") else None,
        height=int(row["height"]) if row.get("height") else None,
        steps=int(row["steps"]) if row.get("steps") else None,
        guidance=float(row["guidance"]) if row.get("guidance") else None,
        model=str(row["model"]) if row.get("model") else None,
        source_id=str(row["id"]) if row.get("id") else None,
        extra={k: v for k, v in row.items() if k not in {
            "prompt", "text", "positive", "negative_prompt", "negative",
            "count", "seed", "width", "height", "steps", "guidance", "model", "id",
        }},
    )


def parse_prompt_file(path: Path) -> list[PromptSpec]:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return parse_prompt_text(path.read_text(encoding="utf-8"))
    if suffix == ".jsonl":
        specs: list[PromptSpec] = []
        with path.open(encoding="utf-8") as handle:
            for line_no, raw in enumerate(handle, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    specs.append(_from_mapping(json.loads(line)))
                except Exception as exc:
                    raise ValueError(f"{path}:{line_no}: {exc}") from exc
        return specs
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "prompts" in payload:
            payload = payload["prompts"]
        if isinstance(payload, list):
            specs = []
            for item in payload:
                if isinstance(item, str):
                    specs.append(PromptSpec(prompt=item))
                elif isinstance(item, dict):
                    specs.append(_from_mapping(item))
                else:
                    raise ValueError(f"Unsupported JSON item: {item!r}")
            return specs
        if isinstance(payload, dict) and "prompt" in payload:
            return [_from_mapping(payload)]
        raise ValueError("JSON must be a list, {prompts: [...]}, or a single prompt object")
    if suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [_from_mapping(dict(row)) for row in reader]
    raise ValueError(f"Unsupported prompt file type: {suffix or path.name}")


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"txt", "jsonl", "json", "csv"}:
        return suffix
    raise ValueError(f"Unsupported prompt file type: {path.suffix}")


def resolve_steps_guidance(spec: PromptSpec, config: JobConfig) -> tuple[int, float]:
    model = get_model(spec.model or config.model)
    steps = spec.steps if spec.steps is not None else config.steps
    guidance = spec.guidance if spec.guidance is not None else config.guidance
    return (
        int(steps if steps is not None else model.default_steps),
        float(guidance if guidance is not None else model.default_guidance),
    )


def expand_seeds(base: int | None, count: int) -> list[int]:
    if count < 1:
        raise ValueError("count must be >= 1")
    start = base if base is not None else _default_seed()
    return [int(start) + i for i in range(count)]


def _default_seed() -> int:
    import secrets

    return secrets.randbelow(2**31 - 1)


def iter_specs(specs: Iterable[PromptSpec]) -> list[PromptSpec]:
    return [spec for spec in specs if spec.prompt.strip()]
