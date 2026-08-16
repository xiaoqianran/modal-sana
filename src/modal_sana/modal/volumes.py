from __future__ import annotations

import modal

CACHE_DIR = "/cache"
MODELS_DIR = "/cache/models"
CACHE_VOLUME_NAME = "modal-sana-hf-cache"
LEDGER_DIR = "/ledger"
LEDGER_VOLUME_NAME = "modal-sana-cost-ledger"
LEDGER_DICT_NAME = "modal-sana-cost-ledger"


def huggingface_cache_volume() -> modal.Volume:
    return modal.Volume.from_name(CACHE_VOLUME_NAME, create_if_missing=True)


def cost_ledger_volume() -> modal.Volume:
    return modal.Volume.from_name(LEDGER_VOLUME_NAME, create_if_missing=True)
