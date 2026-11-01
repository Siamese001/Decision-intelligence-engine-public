"""Vector packing and similarity helpers for semantic cache."""
from __future__ import annotations

import math
import struct


def _pack_vector(vector: list[float]) -> bytes:
    if (
        not vector
        or len(vector) > 4096
        or any(not isinstance(value, (int, float)) for value in vector)
        or any(not math.isfinite(float(value)) for value in vector)
    ):
        raise ValueError("semantic cache vector must be finite and bounded")
    return struct.pack(f"<{len(vector)}f", *(float(value) for value in vector))


def _unpack_vector(value: bytes, dimensions: int) -> list[float]:
    if dimensions < 1 or dimensions > 4096 or len(value) != dimensions * 4:
        raise ValueError("semantic cache vector shape is invalid")
    vector = list(struct.unpack(f"<{dimensions}f", value))
    if any(not math.isfinite(item) for item in vector):
        raise ValueError("semantic cache vector is non-finite")
    return vector


def _similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
