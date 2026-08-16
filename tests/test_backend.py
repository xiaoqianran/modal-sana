from __future__ import annotations

from modal_sana.models.sana.backend import pipeline_class_name
from modal_sana.models.sana.registry import get_model


def test_pipeline_mapping() -> None:
    assert pipeline_class_name("sana-sprint-1.6b") == "SanaSprintPipeline"
    assert pipeline_class_name(get_model("sana-1.5-4.8b")) == "SanaPipeline"
