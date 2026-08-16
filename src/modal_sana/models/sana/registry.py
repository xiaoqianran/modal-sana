from __future__ import annotations

from modal_sana.models.base import ModelSpec
from modal_sana.models.sana.configs import SANA_MODELS

MODELS: dict[str, ModelSpec] = {spec.id: spec for spec in SANA_MODELS}


def get_model(model_id: str) -> ModelSpec:
    try:
        return MODELS[model_id]
    except KeyError as exc:
        known = ", ".join(MODELS)
        raise ValueError(f"Unknown model {model_id!r}. Known: {known}") from exc


def list_models() -> list[ModelSpec]:
    return list(SANA_MODELS)


def native_size(model_id: str) -> tuple[int, int]:
    spec = get_model(model_id)
    return spec.native_width, spec.native_height
