"""Receipt generation and persistence for HITL resolution wave runner."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from tools.hitl_resolution_runner_core.models import WaveReceipt
from tools.hitl_resolution_runner_core.verification import compute_file_digest


def persist_wave_receipt(receipt: WaveReceipt, receipt_root: Path) -> Tuple[Path, str]:
    """Persists wave receipt immutably with exclusive creation (O_EXCL)."""
    receipt_root.mkdir(parents=True, exist_ok=True)
    target_path = receipt_root / f"wave_{receipt.wave}_receipt.json"

    # Strict invariant: receipt paths are immutable and must not be overwritten
    with open(target_path, "x", encoding="utf-8") as f:
        payload = json.dumps(receipt.to_dict(), indent=2, sort_keys=True)
        f.write(payload)

    receipt_digest = compute_file_digest(target_path)
    return target_path, receipt_digest
