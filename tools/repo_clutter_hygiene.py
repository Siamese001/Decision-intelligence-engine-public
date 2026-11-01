"""Repository and reports clutter hygiene CLI utility."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.storage_hygiene_core.clutter import plan_repo_clutter
from tools.storage_hygiene_core.pins import get_main_repo_root, resolve_pins
from tools.storage_hygiene_core.policy import load_storage_retention_policy
from tools.storage_hygiene_core.receipt import create_hygiene_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MAIN_REPO_ROOT = get_main_repo_root(ROOT)


def run_clutter_plan(
    repo_root: Path | None = None,
    *,
    out_path: Path | None = None,
    scheduled: bool = False,
) -> dict:
    """Run reports and root clutter planning and return a sealed receipt."""
    r_root = repo_root or MAIN_REPO_ROOT
    policy = load_storage_retention_policy()
    pins_res = resolve_pins(repo_root=r_root, policy=policy)
    clutter_res = plan_repo_clutter(repo_root=r_root, policy=policy)

    summary = {
        "total_candidates": (
            clutter_res.uncommitted_receipts_count
            + clutter_res.reports_archive_count
            + clutter_res.root_archive_count
        ),
        "total_pinned": clutter_res.reports_kept_referenced_count,
        "reclaimable_bytes": 0,
    }

    report = create_hygiene_report(
        mode="plan",
        scheduled=scheduled,
        policy_digest=policy.digest,
        pins=pins_res.to_dict(),
        summary=summary,
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

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info("Saved clutter hygiene plan to %s", out_path)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repository reports and root clutter hygiene planning."
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Action to perform")

    # plan subcommand
    plan_parser = subparsers.add_parser("plan", help="Generate clutter analysis plan")
    plan_parser.add_argument(
        "--repo-root",
        type=Path,
        default=MAIN_REPO_ROOT,
        help="Path to repository root",
    )
    plan_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Path to write sealed plan receipt JSON",
    )
    plan_parser.add_argument(
        "--scheduled",
        action="store_true",
        default=False,
        help="Indicate that run was triggered on a schedule (read-only enforcement)",
    )

    args = parser.parse_args()

    r_root = getattr(args, "repo_root", MAIN_REPO_ROOT)
    out_file = getattr(args, "out", None)
    scheduled = getattr(args, "scheduled", False)

    report = run_clutter_plan(
        repo_root=r_root,
        out_path=out_file,
        scheduled=scheduled,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
