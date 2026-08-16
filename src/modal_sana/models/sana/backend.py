from __future__ import annotations

from modal_sana.models.base import ModelSpec
from modal_sana.models.sana.registry import get_model


def pipeline_class_name(spec: ModelSpec | str) -> str:
    model = spec if isinstance(spec, ModelSpec) else get_model(spec)
    if model.pipeline == "sana-sprint":
        return "SanaSprintPipeline"
    return "SanaPipeline"
