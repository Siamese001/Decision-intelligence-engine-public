"""
dual_pipeline/receipt_schema.py

Canonical schema and cryptographic sealing engine for immutable runtime execution receipts.
Enforces governance standards defined in AGENTS.md §"Wave progression is receipt-gated".
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class RuntimeReceipt:
    """
    Cryptographically sealed, immutable execution receipt.
    """
    receipt_schema_version: str
    wave_id: str
    plan_version: str
    commit_sha: str
    worktree_clean: bool
    command: str
    exit_code: int
    timestamp: str
    input_artifact_hashes: Dict[str, str]
    output_artifact_hashes: Dict[str, str]
    policy_digest: str
    parent_receipt_hash: Optional[str]
    verdict: str
    validation_metrics: Dict[str, Any] = field(default_factory=dict)

    def compute_receipt_digest(self) -> str:
        """Compute deterministic SHA-256 digest of this receipt payload."""
        data = asdict(self)
        canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Export serialized dictionary with computed receipt_digest."""
        d = asdict(self)
        d["receipt_digest"] = self.compute_receipt_digest()
        return d


class ReceiptFactory:
    """Factory for creating and verifying immutable runtime receipts."""

    @staticmethod
    def get_git_info() -> tuple[str, bool]:
        """Obtain current commit SHA and clean worktree status."""
        try:
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=10.0).strip()
            status = subprocess.check_output(["git", "status", "--porcelain"], text=True, timeout=10.0).strip()
            return sha, len(status) == 0
        except Exception:
            return "0000000000000000000000000000000000000000", False

    @staticmethod
    def hash_file(file_path: Path | str) -> str:
        """Compute SHA-256 of file bytes."""
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            return "FILE_NOT_FOUND"
        return hashlib.sha256(p.read_bytes()).hexdigest()

    @classmethod
    def create_receipt(
        cls,
        wave_id: str,
        plan_version: str,
        command: str,
        exit_code: int,
        input_paths: List[Path | str],
        output_paths: List[Path | str],
        policy_digest: str,
        verdict: str,
        parent_receipt_path: Optional[Path | str] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> RuntimeReceipt:
        """Construct a new runtime execution receipt."""
        commit_sha, worktree_clean = cls.get_git_info()
        now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        in_hashes = {str(p): cls.hash_file(p) for p in input_paths}
        out_hashes = {str(p): cls.hash_file(p) for p in output_paths}

        parent_hash = None
        if parent_receipt_path:
            p_path = Path(parent_receipt_path)
            if p_path.exists():
                try:
                    p_data = json.loads(p_path.read_text(encoding="utf-8"))
                    parent_hash = p_data.get("receipt_digest")
                except Exception:
                    parent_hash = cls.hash_file(p_path)

        return RuntimeReceipt(
            receipt_schema_version="2.0.0",
            wave_id=wave_id,
            plan_version=plan_version,
            commit_sha=commit_sha,
            worktree_clean=worktree_clean,
            command=command,
            exit_code=exit_code,
            timestamp=now_utc,
            input_artifact_hashes=in_hashes,
            output_artifact_hashes=out_hashes,
            policy_digest=policy_digest,
            parent_receipt_hash=parent_hash,
            verdict=verdict,
            validation_metrics=metrics or {},
        )

    @classmethod
    def seal_receipt(
        cls,
        receipt: RuntimeReceipt,
        destination_path: Path | str,
    ) -> Path:
        """Seal receipt to disk with computed digest."""
        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(receipt.to_dict(), indent=2), encoding="utf-8")
        return dest
