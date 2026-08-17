from __future__ import annotations

import io

from PIL import Image

from modal_sana.storage.encode import encode_image


def test_png_speed_preset_stays_lossless() -> None:
    image = Image.new("RGB", (32, 32), (17, 42, 99))
    encoded = encode_image(image, "png")
    decoded = Image.open(io.BytesIO(encoded)).convert("RGB")
    assert decoded.size == image.size
    assert decoded.getpixel((0, 0)) == (17, 42, 99)


def test_other_formats_still_encode() -> None:
    image = Image.new("RGB", (16, 16), (200, 50, 20))
    assert encode_image(image, "jpg", 90)[:2] == b"\xff\xd8"
    webp = encode_image(image, "webp", 90)
    assert webp[:4] == b"RIFF"
