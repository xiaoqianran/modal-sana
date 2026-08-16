from __future__ import annotations

import modal

from modal_sana.modal.image import image

app = modal.App("modal-sana", image=image)
