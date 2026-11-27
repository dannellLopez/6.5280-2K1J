"""Per-frame image codec. Each frame is encoded independently (as in the spec's
initial design) so the client can display any one frame without decoding others."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image


def encode_frame(rgb: np.ndarray, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def decode_frame(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("RGB"))
