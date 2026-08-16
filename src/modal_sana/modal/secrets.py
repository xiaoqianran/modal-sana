from __future__ import annotations

import os
from typing import Any


_TOKEN_KEYS = (
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "CIVITAI_TOKEN",
    "GITHUB_TOKEN",
)


def token_env() -> dict[str, str]:
    """Read download tokens from the process environment. Never log values."""
    data: dict[str, str] = {}
    for key in _TOKEN_KEYS:
        value = os.environ.get(key)
        if value:
            data[key] = value
    if "HF_TOKEN" in data and "HUGGING_FACE_HUB_TOKEN" not in data:
        data["HUGGING_FACE_HUB_TOKEN"] = data["HF_TOKEN"]
    if "HUGGING_FACE_HUB_TOKEN" in data and "HF_TOKEN" not in data:
        data["HF_TOKEN"] = data["HUGGING_FACE_HUB_TOKEN"]
    return data


def hf_token() -> str | None:
    env = token_env()
    return env.get("HF_TOKEN") or env.get("HUGGING_FACE_HUB_TOKEN")


def modal_download_secrets() -> list[Any]:
    """Modal Secret for CPU prefetch. Empty if no tokens are configured."""
    data = token_env()
    if not data:
        return []
    import modal

    return [modal.Secret.from_dict(data)]
