"""
dual_pipeline/ptc/spooler.py

Bounded output marshaller and disk spooler for Programmatic Tool Calling.
Prevents context pollution by persisting oversized results to disk.
Layer: L1 (Physical I/O / Persistence).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional, Tuple

from dual_pipeline.ptc.contracts import (
    PTCResultSummary,
    PTCStatus,
    TokenBudget,
)


class PTCOutputSpooler:
    """Spools oversized tool outputs to disk while emitting bounded summaries."""

    def __init__(self, spool_dir: Optional[Path] = None) -> None:
        self.spool_dir = spool_dir

    def spool(
        self,
        task_id: str,
        data: Any,
        token_budget: Optional[TokenBudget] = None,
        runtime_root: Optional[Path] = None,
    ) -> PTCResultSummary:
        """Direct spooling helper returning summary with artifact_ref."""
        budget = token_budget or TokenBudget(max_payload_bytes=0)
        root = runtime_root or self.spool_dir or Path("/tmp")
        return self.marshal(
            task_id=task_id,
            data=data,
            token_budget=budget,
            runtime_root=root,
        )

    def marshal(
        self,
        *,
        task_id: str,
        data: Any,
        token_budget: TokenBudget,
        runtime_root: Path,
        execution_time_ms: float = 0.0,
    ) -> PTCResultSummary:
        """Marshals result data against TokenBudget, spooling if payload ceiling is exceeded."""
        try:
            payload_json = json.dumps(data, default=str, separators=(",", ":"))
        except Exception as err:
            return PTCResultSummary(
                status=PTCStatus.ERROR,
                task_id=task_id,
                execution_time_ms=execution_time_ms,
                payload_bytes=0,
                error_message=f"JSON_SERIALIZATION_FAILED: {err}",
            )

        payload_bytes = len(payload_json.encode("utf-8"))

        if not token_budget.is_exceeded(payload_bytes):
            return PTCResultSummary(
                status=PTCStatus.SUCCESS,
                task_id=task_id,
                execution_time_ms=execution_time_ms,
                payload_bytes=payload_bytes,
                data=data,
            )

        # Payload exceeds budget ceiling -> Spool to disk
        sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        target_dir = self.spool_dir or (runtime_root / "scratch" / "ptc_spool")
        target_dir.mkdir(parents=True, exist_ok=True)

        spool_file = target_dir / f"{task_id}_{sha256[:16]}.json"
        tmp_file = target_dir / f"{task_id}_{sha256[:16]}.json.tmp"

        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                f.write(payload_json)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, spool_file)
        except Exception as write_err:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            return PTCResultSummary(
                status=PTCStatus.ERROR,
                task_id=task_id,
                execution_time_ms=execution_time_ms,
                payload_bytes=payload_bytes,
                error_message=f"SPOOL_WRITE_FAILED: {write_err}",
            )

        # Generate bounded preview
        preview, count = self._generate_preview(data)

        return PTCResultSummary(
            status=PTCStatus.SPOOLED_POINTER,
            task_id=task_id,
            execution_time_ms=execution_time_ms,
            payload_bytes=payload_bytes,
            data={
                "spooled_summary": preview,
                "total_items": count,
                "original_bytes": payload_bytes,
            },
            artifact_ref=str(spool_file),
            sha256_digest=sha256,
        )

    def _generate_preview(self, data: Any) -> Tuple[Any, int]:
        """Creates a compact preview of the spooled data."""
        if isinstance(data, list):
            return data[:3], len(data)
        elif isinstance(data, dict):
            keys = list(data.keys())
            preview = {k: data[k] for k in keys[:5]}
            return preview, len(keys)
        elif isinstance(data, str):
            preview = data[:200] + "..." if len(data) > 200 else data
            return preview, len(data)
        return str(data)[:200], 1
