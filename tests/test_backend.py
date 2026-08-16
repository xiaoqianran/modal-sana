from __future__ import annotations

from modal_sana.models.sana.backend import pipeline_class_name
from modal_sana.models.sana.registry import get_model


def test_pipeline_mapping() -> None:
    assert pipeline_class_name("sana-sprint-1.6b") == "SanaSprintPipeline"
    assert pipeline_class_name(get_model("sana-1.5-4.8b")) == "SanaPipeline"
    assert pipeline_class_name("sana-1.6b-4k") == "SanaPipeline"


def test_native_resolution_follows_checkpoint() -> None:
    sprint = get_model("sana-sprint-1.6b")
    assert sprint.native_width == 1024
    assert sprint.vae_tiling is False
    one_five = get_model("sana-1.5-1.6b")
    assert one_five.native_width == 1024
    two_k = get_model("sana-1.6b-2k")
    assert two_k.native_width == 2048
    assert two_k.vae_tiling is True
    four_k = get_model("sana-1.6b-4k")
    assert four_k.native_width == 4096
    assert four_k.native_height == 4096
    assert four_k.recommended_batch == 1
    assert four_k.hf_id.endswith("4Kpx_BF16_diffusers")
