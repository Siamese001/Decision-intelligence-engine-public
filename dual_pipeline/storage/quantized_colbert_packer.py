import struct
from typing import Iterable, Tuple


def pack_colbert_tensor(values: Iterable[float]) -> bytes:
    import numpy as np
    arr = np.array(list(values), dtype=np.float32)
    # fp16 quantization reduces footprint by 50%
    # int8 quantization reduces footprint by 75%
    # We'll use int8 with fp32 scale.
    max_val = np.max(np.abs(arr))
    scale = float(max_val) / 127.0 if max_val > 0 else 1.0
    quantized = np.round(arr / scale).astype(np.int8)
    return struct.pack("<f", scale) + quantized.tobytes()

def unpack_colbert_tensor(value: bytes) -> Tuple[float, ...]:
    import struct

    import numpy as np
    if len(value) == 36: # 4 bytes scale + 32 bytes int8
        scale = struct.unpack("<f", value[:4])[0]
        quantized = np.frombuffer(value[4:], dtype=np.int8)
        return tuple(float(x) for x in (quantized.astype(np.float32) * scale))
    # Fallback to standard float32
    return struct.unpack(f"<{len(value) // 4}f", value)

