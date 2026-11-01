"""Quarantine apply and restore execution engine with zero graph mutation proof."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .pins import get_main_repo_root
    from .receipt import digest_payload
except ImportError:
    from tools.storage_hygiene_core.pins import get_main_repo_root
    from tools.storage_hygiene_core.receipt import digest_payload

logger = logging.getLogger(__name__)

MAIN_REPO_ROOT = get_main_repo_root(ROOT)
DEFAULT_TRASH_ROOT = MAIN_REPO_ROOT / ".local" / "trash"


class PlanDriftError(RuntimeError):
    """Raised when the plan digest does not match current state."""


class ScheduledMutationBlockedError(RuntimeError):
    """Raised when an apply is attempted in scheduled mode."""


class MissingApprovalError(RuntimeError):
    """Raised when authentic operator approval receipt is missing or invalid."""


@dataclass
class QuarantineItem:
    original_path: str
    quarantine_path: str
    item_type: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_file(path: Path) -> str:
    """Compute sha256 of file, returning empty string if missing."""
    if not path.exists() or not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _get_active_pair_and_graph_hashes(repo_root: Path) -> tuple[str, str]:
    """Capture sha256 of active_pair.json and its active graph database."""
    pair_path = repo_root / ".local" / "dual_pipeline" / "active_pair.json"
    if not pair_path.exists():
        pair_path = repo_root / "dual_pipeline" / "retrieval" / "active_pair.json"

    if not pair_path.exists():
        return "", ""

    pair_hash = _sha256_file(pair_path)
    try:
        data = json.loads(pair_path.read_text(encoding="utf-8"))
        gpath_str = data.get("graph_path") or data.get("graph")
        if gpath_str:
            gp = Path(gpath_str)
            if not gp.is_absolute():
                gp = pair_path.parent / gp
            graph_hash = _sha256_file(gp)
        else:
            graph_hash = ""
    except Exception:
        graph_hash = ""

    return pair_hash, graph_hash


def validate_approval_receipt(receipt_path: Path) -> dict[str, Any]:
    """Verify that approval receipt exists, is valid JSON, and has operator signature/approval."""
    if not receipt_path.exists():
        raise MissingApprovalError(f"Approval receipt does not exist at {receipt_path}")

    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MissingApprovalError(f"Approval receipt is invalid JSON: {exc}") from exc

    operator = data.get("operator", "").strip() if isinstance(data.get("operator"), str) else ""
    if operator and operator.lower() in ("auto", "scheduler", "cron", "system"):
        raise MissingApprovalError(f"Operator '{operator}' is not an authentic human operator.")

    # Must contain operator or approval signature
    is_valid = (
        data.get("status") in ("APPROVED", "PASS")
        or "operator" in data
        or "signature" in data
        or "alr_id" in data
        or data.get("approved") is True
    )
    if not is_valid:
        raise MissingApprovalError(
            f"Receipt at {receipt_path} does not declare approved operator status"
        )
    return data


def _quarantine_candidates(
    candidates: list[dict[str, Any]], repo_root: Path, dest_dir: Path,
    subdir: str, item_type: str, *, directories: bool,
) -> list[QuarantineItem]:
    quarantined: list[QuarantineItem] = []
    for cand in candidates:
        src_path = Path(cand["path"])
        if not src_path.is_absolute():
            src_path = repo_root / src_path
        exists_as_expected = src_path.is_dir() if directories else src_path.is_file()
        if not exists_as_expected:
            continue
        target = dest_dir / subdir / src_path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(target))
        quarantined.append(QuarantineItem(
            original_path=str(src_path), quarantine_path=str(target),
            item_type=item_type, size_bytes=cand.get("size_bytes", 0),
        ))
    return quarantined


def apply_quarantine(
    plan_report: dict[str, Any],
    approval_receipt_path: Path,
    *,
    expected_plan_digest: str | None = None,
    trash_root: Path | None = None,
    scheduled: bool = False,
    repo_root: Path | None = None,
    authority_context: Any = None,
) -> dict[str, Any]:
    """Move candidates to quarantine directory and record reverse restore receipt."""
    if authority_context is not None:
        if hasattr(authority_context, "check_caller_scheduled_bypass"):
            authority_context.check_caller_scheduled_bypass(scheduled)
        if hasattr(authority_context, "assert_can_mutate"):
            authority_context.assert_can_mutate("storage quarantine apply")
    elif scheduled:
        raise ScheduledMutationBlockedError("Mutations are strictly prohibited in scheduled mode.")

    r_root = (repo_root or MAIN_REPO_ROOT).resolve()
    t_root = (trash_root or (r_root / ".local" / "trash")).resolve()

    # Verify plan digest
    actual_plan_digest = plan_report.get("canonical_digest")
    if expected_plan_digest and actual_plan_digest != expected_plan_digest:
        raise PlanDriftError(
            f"Plan digest mismatch: expected {expected_plan_digest}, got {actual_plan_digest}"
        )

    # Verify approval
    validate_approval_receipt(approval_receipt_path)

    # Capture before hashes
    pair_before, graph_before = _get_active_pair_and_graph_hashes(r_root)

    today_str = dt.datetime.now(dt.timezone.utc).strftime("%m-%d-%Y")
    batch_id = uuid.uuid4().hex[:8]
    dest_dir = t_root / today_str / batch_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    quarantined: list[QuarantineItem] = []

    quarantined.extend(_quarantine_candidates(
        plan_report.get("generations_analysis", {}).get("candidates", []),
        r_root, dest_dir, "generations", "generation", directories=True,
    ))
    quarantined.extend(_quarantine_candidates(
        plan_report.get("transcript_cache_analysis", {}).get("candidates", []),
        r_root, dest_dir, "transcript_cache", "transcript_cache_wav", directories=False,
    ))

    # Capture after hashes & prove zero graph mutation
    pair_after, graph_after = _get_active_pair_and_graph_hashes(r_root)
    if pair_before != pair_after:
        raise RuntimeError(
            f"Zero graph mutation violation: active_pair.json hash changed from {pair_before} to {pair_after}"
        )
    if graph_before != graph_after:
        raise RuntimeError(
            f"Zero graph mutation violation: graph database hash changed from {graph_before} to {graph_after}"
        )

    receipt = {
        "contract_id": "urn:nee:storage-hygiene-apply-receipt:1.0.0",
        "schema_version": "1.0.0",
        "receipt_id": f"apply-receipt-{batch_id}",
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "APPLIED",
        "plan_digest": actual_plan_digest,
        "approval_receipt": str(approval_receipt_path),
        "zero_graph_mutation_verified": True,
        "active_pair_sha256": pair_before,
        "graph_sha256": graph_before,
        "quarantined_items": [q.to_dict() for q in quarantined],
        "total_items_count": len(quarantined),
        "total_bytes_reclaimed": sum(q.size_bytes for q in quarantined),
    }
    receipt["canonical_digest"] = digest_payload(receipt)

    receipt_file = dest_dir / "apply_receipt.json"
    receipt_file.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def restore_quarantine(
    receipt_path: Path,
    *,
    authority_context: Any = None,
) -> dict[str, Any]:
    """Reverse a quarantine batch using its apply receipt."""
    if authority_context is not None:
        if hasattr(authority_context, "assert_can_mutate"):
            authority_context.assert_can_mutate("quarantine restore")

    if not receipt_path.exists():
        raise FileNotFoundError(f"Apply receipt not found at {receipt_path}")

    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    items = receipt_data.get("quarantined_items", [])

    restored_count = 0
    for it in items:
        q_path = Path(it["quarantine_path"])
        orig_path = Path(it["original_path"])
        if q_path.exists():
            orig_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(q_path), str(orig_path))
            restored_count += 1

    receipt_data["status"] = "RESTORED"
    receipt_data["restored_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    receipt_data["restored_items_count"] = restored_count
    receipt_data["canonical_digest"] = digest_payload(receipt_data)

    receipt_path.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
    return receipt_data


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Storage Hygiene Quarantine Apply & Dry-Run CLI")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform a dry-run storage scan and seal manifest without mutating disk",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Repository root path",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        from tools.storage_hygiene_core.clutter import plan_repo_clutter
        from tools.storage_hygiene_core.generations import plan_generations_retention
        from tools.storage_hygiene_core.pins import resolve_pins
        from tools.storage_hygiene_core.policy import load_storage_retention_policy
        from tools.storage_hygiene_core.receipt import create_hygiene_report
        from tools.storage_hygiene_core.transcript_cache import plan_transcript_cache

        r_root = args.repo_root or ROOT
        pol_file = r_root / "config" / "storage_retention_policy.json"
        pol = load_storage_retention_policy(pol_file if pol_file.is_file() else None)
        pins_res = resolve_pins(r_root)
        gen_res = plan_generations_retention(r_root, pol.generations, pins_res)
        tc_res = plan_transcript_cache(policy=pol)
        clutter_res = plan_repo_clutter(r_root, pol)

        report = create_hygiene_report(
            mode="plan",
            scheduled=False,
            policy_digest=pol.digest,
            pins=pins_res.to_dict(),
            summary={
                "generations_reclaimable_bytes": gen_res.reclaimable_bytes,
                "transcript_cache_reclaimable_bytes": tc_res.reclaimable_bytes,
                "reports_clutter_count": len(clutter_res.reports),
                "root_clutter_count": len(clutter_res.root_files),
            },
            generations_analysis=gen_res.to_dict(),
            transcript_cache_analysis=tc_res.to_dict(),
            reports_clutter_analysis={"candidates": [r.to_dict() for r in clutter_res.reports]},
            root_clutter_analysis={"candidates": [r.to_dict() for r in clutter_res.root_files]},
        )
        cand_count = (
            len(gen_res.candidates)
            + len(tc_res.candidates)
            + len(clutter_res.reports)
            + len(clutter_res.root_files)
        )
        print(
            f"✅ Storage hygiene dry-run scan complete: {cand_count} candidate items identified across scopes A-C."
        )
        print(f"   Manifest digest: {report.get('canonical_digest', 'N/A')}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
