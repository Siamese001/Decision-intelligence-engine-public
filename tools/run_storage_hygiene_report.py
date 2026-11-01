"""Scheduled and interactive unified storage hygiene report runner."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.storage_hygiene_core.clutter import plan_repo_clutter
from tools.storage_hygiene_core.generations import plan_generations_retention
from tools.storage_hygiene_core.pins import get_main_repo_root, resolve_pins
from tools.storage_hygiene_core.policy import load_storage_retention_policy
from tools.storage_hygiene_core.receipt import create_hygiene_report
from tools.storage_hygiene_core.transcript_cache import plan_transcript_cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MAIN_REPO_ROOT = get_main_repo_root(ROOT)
DEFAULT_RUNTIME_ROOT = MAIN_REPO_ROOT / ".local" / "dual_pipeline"


def run_unified_hygiene_report(
    *,
    scheduled: bool = False,
    repo_root: Path | None = None,
    runtime_root: Path | None = None,
    out_path: Path | None = None,
) -> dict:
    """Execute all read-only storage and clutter planners and emit a single sealed report."""
    r_root = (repo_root or MAIN_REPO_ROOT).resolve()
    rt_root = (runtime_root or (r_root / ".local" / "dual_pipeline")).resolve()

    policy = load_storage_retention_policy()
    pins_res = resolve_pins(repo_root=r_root, runtime_root=rt_root, policy=policy)

    # 1. Generations analysis
    gen_res = plan_generations_retention(runtime_root=rt_root, policy=policy, pins=pins_res)

    # 2. Transcript cache analysis
    cache_res = plan_transcript_cache(
        cache_dir=r_root / "output" / "transcript_cache",
        policy=policy,
    )

    # 3. Clutter analysis
    clutter_res = plan_repo_clutter(repo_root=r_root, policy=policy)

    total_candidates = (
        gen_res.candidate_count
        + cache_res.candidate_count
        + clutter_res.uncommitted_receipts_count
        + clutter_res.reports_archive_count
        + clutter_res.root_archive_count
    )
    total_pinned = (
        gen_res.pinned_count
        + cache_res.preserved_files_count
        + clutter_res.reports_kept_referenced_count
    )
    reclaimable_bytes = gen_res.reclaimable_bytes + cache_res.reclaimable_bytes

    summary = {
        "total_candidates": total_candidates,
        "total_pinned": total_pinned,
        "reclaimable_bytes": reclaimable_bytes,
        "reclaimable_gb": round(reclaimable_bytes / (1024**3), 2),
    }

    report = create_hygiene_report(
        mode="plan",
        scheduled=scheduled,
        policy_digest=policy.digest,
        pins=pins_res.to_dict(),
        summary=summary,
        generations_analysis=gen_res.to_dict(),
        transcript_cache_analysis=cache_res.to_dict(),
        reports_clutter_analysis={
            "uncommitted_receipts_count": clutter_res.uncommitted_receipts_count,
            "reports_archive_count": clutter_res.reports_archive_count,
            "reports_kept_referenced_count": clutter_res.reports_kept_referenced_count,
            "uncommitted_receipts": [r.to_dict() for r in clutter_res.uncommitted_receipts],
            "reports_archive_candidates": [
                r.to_dict() for r in clutter_res.reports if r.action == "ARCHIVE_MOVE"
            ],
            "reports_kept_referenced": [
                r.to_dict() for r in clutter_res.reports if r.action == "KEEP_REFERENCED"
            ],
        },
        root_clutter_analysis={
            "root_archive_count": clutter_res.root_archive_count,
            "root_review_count": clutter_res.root_review_count,
            "archive_candidates": [
                rf.to_dict() for rf in clutter_res.root_files if rf.category == "ARCHIVE_CANDIDATE"
            ],
            "review_candidates": [
                rf.to_dict() for rf in clutter_res.root_files if rf.category == "REVIEW"
            ],
            "protected_files": [
                rf.to_dict() for rf in clutter_res.root_files if rf.category == "KEEP"
            ],
        },
    )

    if out_path is None:
        today_str = dt.datetime.now(dt.timezone.utc).strftime("%m-%d-%Y")
        out_path = r_root / ".local" / "storage_hygiene" / "reports" / f"hygiene_report_{today_str}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Saved unified storage hygiene report to %s", out_path)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified storage and clutter hygiene report runner."
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        default=False,
        help="Run in unattended scheduled mode (hard read-only enforcement)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=MAIN_REPO_ROOT,
        help="Repository root path",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
        help="Dual pipeline runtime root",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to output JSON report",
    )
    args = parser.parse_args()

    report = run_unified_hygiene_report(
        scheduled=args.scheduled,
        repo_root=args.repo_root,
        runtime_root=args.runtime_root,
        out_path=args.out,
    )

    summary = report["summary"]
    print("\n" + "=" * 60)
    print("  NEE STORAGE & CLUTTER HYGIENE REPORT")
    print(f"  Mode: {'SCHEDULED (REPORT-ONLY)' if args.scheduled else 'INTERACTIVE PLAN'}")
    print(f"  Total Candidates: {summary['total_candidates']}")
    print(f"  Reclaimable Space: {summary['reclaimable_gb']} GB ({summary['reclaimable_bytes']} bytes)")
    print(f"  Pinned & Preserved: {summary['total_pinned']}")
    print("=" * 60)
    print(f"Report ID: {report['report_id']}")
    print(f"Canonical Digest: {report['canonical_digest']}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
