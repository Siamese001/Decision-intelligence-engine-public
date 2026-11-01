"""Storage retention policy schema and loader."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = ROOT / "config" / "storage_retention_policy.json"


class PolicyValidationError(ValueError):
    """Raised when policy values violate allowed boundaries."""


@dataclass(frozen=True)
class GenerationPolicy:
    rollback_depth: int = 3
    min_age_days: int = 7
    preserve_receipt_cited: bool = True

    def validate(self) -> None:
        if self.rollback_depth < 1:
            raise PolicyValidationError(
                f"rollback_depth must be >= 1, got {self.rollback_depth}"
            )
        if self.min_age_days < 0:
            raise PolicyValidationError(
                f"min_age_days must be >= 0, got {self.min_age_days}"
            )


@dataclass(frozen=True)
class TranscriptCachePolicy:
    wav_ttl_days: int = 7
    keep_derived_text_and_json: bool = True

    def validate(self) -> None:
        if self.wav_ttl_days < 1:
            raise PolicyValidationError(
                f"wav_ttl_days must be >= 1, got {self.wav_ttl_days}"
            )


@dataclass(frozen=True)
class ReportsPolicy:
    archive_age_days: int = 14
    archive_root: str = "reports/history"

    def validate(self) -> None:
        if self.archive_age_days < 1:
            raise PolicyValidationError(
                f"archive_age_days must be >= 1, got {self.archive_age_days}"
            )
        if not self.archive_root:
            raise PolicyValidationError("archive_root must not be empty")


@dataclass(frozen=True)
class QuarantinePolicy:
    root: str = ".local/trash"
    purge_ttl_days: int = 7

    def validate(self) -> None:
        if not self.root:
            raise PolicyValidationError("quarantine root must not be empty")
        if self.purge_ttl_days < 1:
            raise PolicyValidationError(
                f"purge_ttl_days must be >= 1, got {self.purge_ttl_days}"
            )


@dataclass(frozen=True)
class StorageRetentionPolicy:
    version: str = "1.0.0"
    generations: GenerationPolicy = field(default_factory=GenerationPolicy)
    transcript_cache: TranscriptCachePolicy = field(default_factory=TranscriptCachePolicy)
    reports: ReportsPolicy = field(default_factory=ReportsPolicy)
    quarantine: QuarantinePolicy = field(default_factory=QuarantinePolicy)
    never_touch: tuple[str, ...] = (
        "env_di",
        ".env*",
        "hitl_decision_ledger.json",
        "hitl_decision_ledger.sqlite",
        "release_state.json",
        "active_pair.json",
        "*.sqlite",
        "*.sqlite-wal",
        "*.sqlite-shm",
        "*.db",
    )
    raw_payload: dict[str, Any] = field(default_factory=dict, repr=False)
    digest: str = ""

    def validate(self) -> None:
        self.generations.validate()
        self.transcript_cache.validate()
        self.reports.validate()
        self.quarantine.validate()
        if not self.never_touch:
            raise PolicyValidationError("never_touch must contain protected file globs")


def load_storage_retention_policy(
    policy_path: Path | None = None,
) -> StorageRetentionPolicy:
    """Load, validate, and compute digest of the storage retention policy."""
    target = policy_path or DEFAULT_POLICY_PATH
    if not target.exists():
        # Fall back to safe in-memory defaults with empty digest
        policy = StorageRetentionPolicy()
        policy.validate()
        return policy

    raw_text = target.read_text(encoding="utf-8")
    payload = json.loads(raw_text)

    # Compute deterministic sha256 digest over normalized JSON bytes
    canonical_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(canonical_bytes).hexdigest()

    gen_raw = payload.get("generations", {})
    gen_pol = GenerationPolicy(
        rollback_depth=int(gen_raw.get("rollback_depth", 3)),
        min_age_days=int(gen_raw.get("min_age_days", 7)),
        preserve_receipt_cited=bool(gen_raw.get("preserve_receipt_cited", True)),
    )

    tc_raw = payload.get("transcript_cache", {})
    tc_pol = TranscriptCachePolicy(
        wav_ttl_days=int(tc_raw.get("wav_ttl_days", 7)),
        keep_derived_text_and_json=bool(tc_raw.get("keep_derived_text_and_json", True)),
    )

    rep_raw = payload.get("reports", {})
    rep_pol = ReportsPolicy(
        archive_age_days=int(rep_raw.get("archive_age_days", 14)),
        archive_root=str(rep_raw.get("archive_root", "reports/history")),
    )

    q_raw = payload.get("quarantine", {})
    q_pol = QuarantinePolicy(
        root=str(q_raw.get("root", ".local/trash")),
        purge_ttl_days=int(q_raw.get("purge_ttl_days", 7)),
    )

    never_touch = tuple(payload.get("never_touch", ()))
    if not never_touch:
        never_touch = StorageRetentionPolicy().never_touch

    policy = StorageRetentionPolicy(
        version=str(payload.get("version", "1.0.0")),
        generations=gen_pol,
        transcript_cache=tc_pol,
        reports=rep_pol,
        quarantine=q_pol,
        never_touch=never_touch,
        raw_payload=payload,
        digest=digest,
    )
    policy.validate()
    return policy
