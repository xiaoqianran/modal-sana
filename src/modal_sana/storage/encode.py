from __future__ import annotations

import io

from PIL import Image


def encode_image(image: Image.Image, image_format: str = "png", quality: int = 90) -> bytes:
    buffer = io.BytesIO()
    fmt = image_format.lower()
    if fmt == "webp":
        # Speed-oriented: method=6 is substantially more CPU-heavy and the
        # encoder runs inside the billed GPU container. Keep visual quality
        # unchanged while trading a little file size for lower GPU wall time.
        image.save(buffer, format="WEBP", quality=quality, method=2)
    elif fmt in {"jpg", "jpeg"}:
        rgb = image.convert("RGB")
        rgb.save(buffer, format="JPEG", quality=quality, optimize=False)
    else:
        # PNG remains lossless. compress_level=1 avoids spending expensive
        # L40S seconds chasing a smaller file with optimize=True.
        image.save(buffer, format="PNG", compress_level=1, optimize=False)
    return buffer.getvalue()
