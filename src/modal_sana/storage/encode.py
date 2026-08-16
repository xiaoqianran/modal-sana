from __future__ import annotations

import io

from PIL import Image


def encode_image(image: Image.Image, image_format: str = "webp", quality: int = 90) -> bytes:
    buffer = io.BytesIO()
    fmt = image_format.lower()
    if fmt == "webp":
        image.save(buffer, format="WEBP", quality=quality, method=6)
    elif fmt in {"jpg", "jpeg"}:
        rgb = image.convert("RGB")
        rgb.save(buffer, format="JPEG", quality=quality, optimize=True)
    else:
        image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
