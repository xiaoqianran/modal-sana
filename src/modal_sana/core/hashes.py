from __future__ import annotations

import hashlib
import json
import re


_WHITESPACE = re.compile(r"\s+")


def normalize_prompt(prompt: str) -> str:
    return _WHITESPACE.sub(" ", prompt.strip().lower())


def task_hash(
    *,
    prompt: str,
    negative_prompt: str,
    seed: int,
    model: str,
    width: int,
    height: int,
    steps: int,
    guidance: float,
    image_format: str,
) -> str:
    payload = {
        "prompt": normalize_prompt(prompt),
        "negative_prompt": normalize_prompt(negative_prompt),
        "seed": seed,
        "model": model,
        "width": width,
        "height": height,
        "steps": steps,
        "guidance": round(float(guidance), 4),
        "format": image_format,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
