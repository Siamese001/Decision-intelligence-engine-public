"""Runtime storage garbage collection and retention utility for NEE Dual Pipeline."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.storage_hygiene_core.generations import (
    ActivationInProgressError,
    plan_generations_retention,
)
from tools.storage_hygiene_core.pins import resolve_pins
from tools.storage_hygiene_core.policy import load_storage_retention_policy
from tools.storage_hygiene_core.receipt import create_hygiene_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_RUNTIME_ROOT = ROOT / ".local" / "dual_pipeline"


def run_generations_plan(
    runtime_root: Path,
    *,
    out_path: Path | None = None,
    scheduled: bool = False,
) -> dict:
    """Run generation retention planning and return a sealed receipt."""
    policy = load_storage_retention_policy()
    pins_res = resolve_pins(runtime_root=runtime_root, policy=policy)

    if pins_res.fail_closed:
        logger.error("Pin resolution failed closed: %s", pins_res.fail_reason)
        return {
            "error": "pin_resolution_failed_closed",
            "reason": pins_res.fail_reason,
        }

    try:
        plan_res = plan_generations_retention(
            runtime_root=runtime_root,
            policy=policy,
            pins=pins_res,
        )
    except ActivationInProgressError as exc:
        logger.warning("Generation planning blocked: %s", exc)
        return {
            "error": "activation_in_progress",
            "message": str(exc),
        }

    summary = {
        "total_candidates": plan_res.candidate_count,
        "total_pinned": plan_res.pinned_count,
        "reclaimable_bytes": plan_res.reclaimable_bytes,
    }

    report = create_hygiene_report(
        mode="plan",
        scheduled=scheduled,
        policy_digest=policy.digest,
        pins=pins_res.to_dict(),
        summary=summary,
        generations_analysis=plan_res.to_dict(),
    )

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info("Saved generation retention plan to %s", out_path)

    return report


def run_transcript_cache_plan(
    cache_dir: Path | None = None,
    *,
    out_path: Path | None = None,
    scheduled: bool = False,
) -> dict:
    """Run transcript cache retention planning and return a sealed receipt."""
    from tools.storage_hygiene_core.transcript_cache import plan_transcript_cache

    policy = load_storage_retention_policy()
    pins_res = resolve_pins(policy=policy)
    cache_res = plan_transcript_cache(cache_dir=cache_dir, policy=policy)

    summary = {
        "total_candidates": cache_res.candidate_count,
        "total_pinned": cache_res.preserved_files_count,
        "reclaimable_bytes": cache_res.reclaimable_bytes,
    }

    report = create_hygiene_report(
        mode="plan",
        scheduled=scheduled,
        policy_digest=policy.digest,
        pins=pins_res.to_dict(),
        summary=summary,
        transcript_cache_analysis=cache_res.to_dict(),
    )

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info("Saved transcript cache plan to %s", out_path)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dual Pipeline runtime storage garbage collection and retention."
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Action to perform")

    # plan subcommand (generations)
    plan_parser = subparsers.add_parser("plan", help="Generate retention plan for graph generations")
    plan_parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
        help="Path to dual pipeline runtime root",
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

    # plan-cache subcommand (transcript cache)
    cache_parser = subparsers.add_parser("plan-cache", help="Generate retention plan for transcript cache audio files")
    cache_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Path to output/transcript_cache directory",
    )
    cache_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Path to write sealed plan receipt JSON",
    )
    cache_parser.add_argument(
        "--scheduled",
        action="store_true",
        default=False,
        help="Indicate that run was triggered on a schedule (read-only enforcement)",
    )

    args = parser.parse_args()

    if args.subcommand == "plan-cache":
        report = run_transcript_cache_plan(
            cache_dir=args.cache_dir,
            out_path=args.out,
            scheduled=args.scheduled,
        )
        print(json.dumps(report, indent=2))
    elif args.subcommand is None or args.subcommand == "plan":
        rt_root = getattr(args, "runtime_root", DEFAULT_RUNTIME_ROOT)
        out_file = getattr(args, "out", None)
        scheduled = getattr(args, "scheduled", False)
        report = run_generations_plan(
            runtime_root=rt_root,
            out_path=out_file,
            scheduled=scheduled,
        )
        print(json.dumps(report, indent=2))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
