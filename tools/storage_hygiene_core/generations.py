"""Generation Retention Planner for Dual Pipeline Storage."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .pins import PinResolutionResult, resolve_pins
from .policy import StorageRetentionPolicy, load_storage_retention_policy

logger = logging.getLogger(__name__)


class ActivationInProgressError(RuntimeError):
    """Raised when active_pair.lock indicates an ongoing graph activation."""


@dataclass
class GenerationClassification:
    generation_id: str
    category: str
    size_bytes: int
    path: str
    reason: str
    tombstone: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GenerationPlanResult:
    total_generations_count: int = 0
    total_generations_bytes: int = 0
    candidate_count: int = 0
    reclaimable_bytes: int = 0
    pinned_count: int = 0
    classifications: list[GenerationClassification] = field(default_factory=list)
    loose_files: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def candidates(self) -> list[GenerationClassification]:
        return [c for c in self.classifications if c.category == "CANDIDATE"]

    @property
    def pinned(self) -> list[GenerationClassification]:
        return [c for c in self.classifications if c.category != "CANDIDATE"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_generations_count": self.total_generations_count,
            "total_generations_bytes": self.total_generations_bytes,
            "candidate_count": self.candidate_count,
            "reclaimable_bytes": self.reclaimable_bytes,
            "pinned_count": self.pinned_count,
            "candidates": [c.to_dict() for c in self.candidates],
            "pinned": [c.to_dict() for c in self.pinned],
            "loose_files": self.loose_files,
            "findings": self.findings,
        }


def _calc_dir_size_bytes(dir_path: Path) -> int:
    """Recursively calculate directory size in bytes without external tools."""
    total = 0
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += _calc_dir_size_bytes(Path(entry.path))
                except (OSError, FileNotFoundError):
                    continue
    except (OSError, FileNotFoundError):
        pass
    return total


def _read_generation_tombstone(gen_dir: Path, gen_id: str) -> dict[str, Any]:
    """Read metadata from pair_manifest.json to preserve auditability."""
    manifest_path = gen_dir / "pair_manifest.json"
    tombstone: dict[str, Any] = {
        "generation_id": gen_id,
        "graph_sha256": None,
        "sidecar_sha256": None,
        "model_id": None,
        "previous_generation_id": None,
    }
    if manifest_path.exists():
        try:
            mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
            tombstone["graph_sha256"] = mdata.get("graph_sha256")
            tombstone["sidecar_sha256"] = mdata.get("sidecar_sha256")
            tombstone["model_id"] = mdata.get("model_id")
            tombstone["previous_generation_id"] = mdata.get("previous_generation_id")
            tombstone["activated_at"] = mdata.get("activated_at")
        except Exception:
            pass
    return tombstone


def _check_active_pair_lock(runtime_root: Path, result: GenerationPlanResult) -> None:
    lock_file = runtime_root / "active_pair.lock"
    if not lock_file.exists():
        return
    try:
        age = time.time() - lock_file.stat().st_mtime
        if age < 120.0:
            raise ActivationInProgressError(
                f"active_pair.lock exists at {lock_file} (age={age:.1f}s)"
            )
        result.findings.append({
            "type": "STALE_LOCK",
            "path": str(lock_file),
            "age_seconds": round(age, 1),
        })
    except (OSError, FileNotFoundError):
        pass


def _classify_generation(
    gen_id: str,
    entry: Path,
    p_res: PinResolutionResult,
    pol: StorageRetentionPolicy,
    now: float,
    min_age_seconds: float,
) -> tuple[str, str]:
    if gen_id in p_res.active_generations:
        return "PINNED_ACTIVE", "Active generation in active_pair.json"
    if gen_id in p_res.rollback_generations:
        return "PINNED_ROLLBACK", f"Within rollback depth {pol.generations.rollback_depth}"
    if gen_id in p_res.preserved_generations:
        return "PINNED_PRESERVED", "Preserved in preserved_untracked backups"
    if gen_id in p_res.receipt_cited_generations and pol.generations.preserve_receipt_cited:
        return "RECEIPT_CITED_HISTORICAL", "Referenced in durable release or evaluation receipts"

    try:
        age_seconds = now - entry.stat().st_mtime
    except OSError:
        age_seconds = min_age_seconds + 1

    if age_seconds < min_age_seconds:
        return "TOO_RECENT", f"Created/modified within min age {pol.generations.min_age_days}d"
    return "CANDIDATE", "Eligible for quarantine"


def plan_generations_retention(
    runtime_root: Path,
    policy: StorageRetentionPolicy | None = None,
    pins: PinResolutionResult | None = None,
) -> GenerationPlanResult:
    """Plan retention and cleanup for generation directories under runtime_root."""
    pol = policy or load_storage_retention_policy()
    p_res = pins or resolve_pins(runtime_root=runtime_root, policy=pol)

    result = GenerationPlanResult()
    gens_dir = runtime_root / "generations"

    _check_active_pair_lock(runtime_root, result)

    if not gens_dir.exists():
        return result

    now = time.time()
    min_age_seconds = pol.generations.min_age_days * 86400.0

    entries = sorted(gens_dir.iterdir(), key=lambda p: p.name)
    for entry in entries:
        if not entry.is_dir():
            try:
                fsize = entry.stat().st_size
            except OSError:
                fsize = 0
            result.loose_files.append({
                "name": entry.name,
                "path": str(entry),
                "size_bytes": fsize,
                "category": "REVIEW_LOOSE_FILE",
            })
            continue

        gen_id = entry.name
        size_bytes = _calc_dir_size_bytes(entry)
        result.total_generations_count += 1
        result.total_generations_bytes += size_bytes

        tombstone = _read_generation_tombstone(entry, gen_id)
        category, reason = _classify_generation(
            gen_id, entry, p_res, pol, now, min_age_seconds
        )

        classification = GenerationClassification(
            generation_id=gen_id,
            category=category,
            size_bytes=size_bytes,
            path=str(entry),
            reason=reason,
            tombstone=tombstone,
        )
        result.classifications.append(classification)

        if category == "CANDIDATE":
            result.candidate_count += 1
            result.reclaimable_bytes += size_bytes
        else:
            result.pinned_count += 1

    return result
