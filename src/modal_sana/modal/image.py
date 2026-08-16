from __future__ import annotations

import modal

from modal_sana.modal.volumes import CACHE_DIR

_CACHE_ENV = {
    "HF_XET_HIGH_PERFORMANCE": "1",
    "HF_HUB_CACHE": CACHE_DIR,
    "TRANSFORMERS_CACHE": CACHE_DIR,
}

# CPU prefetch image: huggingface_hub only. No torch, no GPU.
download_image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install("huggingface-hub>=0.30.0")
    .env(_CACHE_ENV)
    .add_local_python_source("modal_sana")
)

# Keep inference deps on the GPU image only. The local CLI never imports torch.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(
        "accelerate>=1.2.0",
        "diffusers>=0.33.0",
        "huggingface-hub>=0.30.0",
        "pillow>=11.0.0",
        "safetensors>=0.5.0",
        "sentencepiece>=0.2.0",
        "torch>=2.5.0",
        "torchvision>=0.20.0",
        "transformers>=4.46.0",
    )
    .env(_CACHE_ENV)
    .add_local_python_source("modal_sana")
)
