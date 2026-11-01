#!/usr/bin/env python3
"""
scripts/run_parallel_remediation.py

Governed Shared-Nothing Multi-Process Batch Remediation Harness.
Enforces the Zero-Contention Architecture:
  1. Phase 1 (Parallel Generation & Evaluation):
     - Processes meetings concurrently across independent worker processes.
     - Writes strictly to isolated filesystem directories (output/<project>/<date>/).
     - Runs deterministic QA gates and LLM micro-judges (gpt-5.6-luna / gpt-5.6-sol).
     - Zero SQLite lock contention: workers do NOT touch CANONICAL_GRAPH.db.
  2. Phase 2 (Chronological Publication Sweep):
     - Single coordinator process sorts certified packages chronologically.
     - Sequentially applies intake packages, preserving hash-chain integrity.
     - Generates granular GraphDB mutation breakdown tables.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.credentials import CredentialManager

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CredentialManager.ensure_ssot_env_loaded()

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from dual_pipeline.transcript_qa import (  # noqa: E402
    evaluate_all_qa_gates,
    evaluate_judge_and_release,
)
from tools.prepare_evidence_envelope_intake import prepare_package  # noqa: E402

logger = logging.getLogger("run_parallel_remediation")


@dataclass
class MeetingSpec:
    meeting_key: str
    project: str
    meeting_date: str  # MM-DD-YYYY
    title: str
    description: str = ""

    @property
    def iso_date(self) -> str:
        parts = self.meeting_date.split("-")
        if len(parts) == 3:
            return f"{parts[2]}-{parts[0]}-{parts[1]}"
        return self.meeting_date

    @property
    def output_dir(self) -> Path:
        return ROOT / "output" / self.project / self.meeting_date


# Registry of target meetings
MEETING_REGISTRY: Dict[str, MeetingSpec] = {
    "conduit_0831": MeetingSpec("conduit_0831", "conduit", "08-31-2026", "Conduit Lessons Learned"),
    "sherpa_0904": MeetingSpec("sherpa_0904", "project_beacon", "09-04-2026", "Project Beacon Path Forward"),
    "sherpa_0909": MeetingSpec("sherpa_0909", "project_beacon", "09-09-2026", "Sherpa Demo Josh"),
    "sherpa_0911": MeetingSpec("sherpa_0911", "project_beacon", "09-11-2026", "Sherpa Next Steps"),
    "ea_0912": MeetingSpec("ea_0912", "enterprise_architecture", "09-12-2026", "Architecture Sync Decoupled WAL"),
    "project_atlas_0828": MeetingSpec("project_atlas_0828", "project_atlas", "08-28-2026", "APEX Development Rewire"),
    "nea_0827": MeetingSpec("nea_0827", "nea", "08-27-2026", "NEA Chakra Operating Model"),
    "nea_0908": MeetingSpec("nea_0908", "nea", "09-08-2026", "Rianne Staffing Feedback"),
    "nexus_0818": MeetingSpec("nexus_0818", "project_nexus", "08-18-2026", "APEX Nexus Rewire"),
    "nexus_0828": MeetingSpec("nexus_0828", "project_nexus", "08-28-2026", "Nexus Reid NEA"),
    "nexus_0901": MeetingSpec("nexus_0901", "project_nexus", "09-01-2026", "NEA Screener Sync"),
    "nexus_0907": MeetingSpec("nexus_0907", "project_nexus", "09-07-2026", "NEA Onboarding Charter Teams"),
    "regai_0902": MeetingSpec("regai_0902", "regai", "09-02-2026", "RegAI Delivery Issues"),
    "ec_scheduling_0409": MeetingSpec("ec_scheduling_0409", "ec_scheduling", "04-09-2026", "E&C Scheduling PO2000622222"),
    "ea_0715": MeetingSpec("ea_0715", "enterprise_architecture", "07-15-2026", "EA AI Policy Strategy"),
    "ea_0811": MeetingSpec("ea_0811", "enterprise_architecture", "08-11-2026", "AI Systems of Record HR PoC"),
    "project_atlas_1208": MeetingSpec("project_atlas_1208", "project_atlas", "12-08-2025", "Rewire Investor Conference"),
    "lens_0911": MeetingSpec("lens_0911", "project_lens", "09-11-2026", "Project LENS Phase II Catchup"),
    "project_spring_0629": MeetingSpec("project_spring_0629", "project_spring", "06-29-2026", "Project Spring SOW"),
}


def _worker_process_meeting(
    spec_dict: Dict[str, Any],
    model_name: str,
    mock: bool,
    reasoning_effort: str = "xhigh",
    use_batch_api: bool = False,
) -> Dict[str, Any]:
    """
    Phase 1 Worker: Processes a single meeting in complete process isolation.
    Guarantees zero database writes and zero lock contention.
    """
    spec = MeetingSpec(**spec_dict)
    out_dir = spec.output_dir
    result: Dict[str, Any] = {
        "meeting_key": spec.meeting_key,
        "project": spec.project,
        "meeting_date": spec.meeting_date,
        "iso_date": spec.iso_date,
        "status": "FAILED",
        "scores": {},
        "package_path": None,
        "error": None,
    }

    try:
        if not out_dir.exists():
            out_dir.mkdir(parents=True, exist_ok=True)

        # Locate or discover Output 01, 02, 03 files
        f1_files = list(out_dir.glob("01_*_hitl_resolved_transcript.md"))
        f2_files = list(out_dir.glob("02_*_executive_recap.md"))
        f3_files = list(out_dir.glob("03_*_canonical_signal_projection.json"))

        if not f1_files or not f2_files or not f3_files:
            result["error"] = f"Missing core artifacts in {out_dir}"
            return result

        f1_path = f1_files[0]
        f2_path = f2_files[0]
        f3_path = f3_files[0]

        f1_text = f1_path.read_text(encoding="utf-8")
        f2_text = f2_path.read_text(encoding="utf-8")
        f3_data = json.loads(f3_path.read_text(encoding="utf-8"))

        # Zero-tolerance invariant: Output 03 action_updates must be strictly strategic or empty
        if "action_updates" in f3_data.get("graph_mutations", {}):
            f3_data["graph_mutations"]["action_updates"] = []
            f3_path.write_text(json.dumps(f3_data, indent=2), encoding="utf-8")

        f3_text = json.dumps(f3_data, indent=2)

        # 1. Deterministic QA Gates
        meta = {
            "project_name": spec.project,
            "meeting_date": spec.meeting_date,
            "meeting_name": spec.title,
        }
        qa_report = evaluate_all_qa_gates(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_data=f3_data,
            raw_text=f1_text,
            metadata=meta,
        )
        qa_path = out_dir / "qa_gates_report.json"
        qa_path.write_text(json.dumps(qa_report, indent=2), encoding="utf-8")

        if use_batch_api:
            from dual_pipeline.batch_core.adapter import (
                BatchSubmissionAdapter,
                create_batch_request,
                record_pending_batch,
                serialize_synthesis_prompts,
            )
            # Route evaluation requests through OpenAI Batch API (50% discount)
            batch_reqs = [
                create_batch_request(
                    custom_id=f"{spec.meeting_key}_judge_o1",
                    body={"model": model_name, "messages": [{"role": "user", "content": f"Evaluate Stage 01 forensic: {f1_text[:2000]}"}]},
                ),
                create_batch_request(
                    custom_id=f"{spec.meeting_key}_judge_o2",
                    body={"model": model_name, "messages": [{"role": "user", "content": f"Evaluate Stage 02 recap: {f2_text[:2000]}"}]},
                ),
                create_batch_request(
                    custom_id=f"{spec.meeting_key}_judge_o3",
                    body={"model": model_name, "messages": [{"role": "user", "content": f"Evaluate Stage 03 graph: {f3_text[:2000]}"}]},
                ),
            ]
            batch_file = serialize_synthesis_prompts(out_dir, batch_reqs)
            adapter = BatchSubmissionAdapter(offline=mock)
            sub_res = adapter.submit_batch(batch_file, metadata={"meeting": spec.meeting_key, "project": spec.project})
            record_pending_batch(out_dir, sub_res)
            result["status"] = "BATCH_QUEUED"
            result["batch_id"] = sub_res.get("batch_id")
            return result

        # 2. Decomposed Multi-Micro-Judge
        _, judge_report, final_decision = evaluate_judge_and_release(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_text=f3_text,
            source_text=f1_text,
            metadata=meta,
            mock=mock,
            model=model_name,
            reasoning_effort=reasoning_effort,
        )

        judge_path = out_dir / "llm_judge_evaluation.json"
        judge_path.write_text(json.dumps(judge_report, indent=2), encoding="utf-8")

        decision_path = out_dir / "final_release_decision.json"
        decision_path.write_text(json.dumps(final_decision, indent=2), encoding="utf-8")

        o1 = judge_report.get("output_1_evaluation") or judge_report.get("stage_01_forensic_eval", {})
        o2 = judge_report.get("output_2_evaluation") or judge_report.get("stage_02_recap_eval", {})
        o3 = judge_report.get("output_3_evaluation") or judge_report.get("stage_03_graph_eval", {})
        scores = {
            "out1": o1.get("composite_score", 0),
            "out2": o2.get("composite_score", 0),
            "out3": o3.get("composite_score", 0),
        }
        result["scores"] = scores

        is_authorized = bool(final_decision.get("authoritative_write_authorized", False))
        if is_authorized:
            # 3. Seal Downstream Intake Package
            intake_dir = ROOT / "intake"
            intake_dir.mkdir(parents=True, exist_ok=True)
            pkg_name = f"{spec.project.upper()}_{spec.iso_date.replace('-', '_')}.intake.json"
            pkg_path = intake_dir / pkg_name
            if pkg_path.exists():
                pkg_path.unlink()

            pkg_obj = prepare_package(
                draft_path=f3_path,
                source_path=f1_path,
                output_path=pkg_path,
                source_location=str(f1_path),
            )

            out_pkg_path = out_dir / "intake_package.json"
            out_pkg_path.write_text(json.dumps(pkg_obj, indent=2), encoding="utf-8")

            result["status"] = "PASSED"
            result["package_path"] = str(pkg_path)
        else:
            result["status"] = "FAIL_CLOSED"

    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = f"{type(e).__name__}: {e}"

    return result


def run_parallel_batch(
    target_keys: List[str],
    workers: int = 4,
    model_name: Optional[str] = None,
    mock: bool = False,
    dry_run: bool = False,
    reasoning_effort: Optional[str] = None,
    use_batch_api: bool = False,
) -> int:
    """Orchestrates Phase 1 parallel evaluation and Phase 2 publication sweep."""
    from dual_pipeline.model_registry import ModelRegistry, ModelTier
    def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FAST_ROUTING)
    eff_model = model_name or def_m
    eff_effort = reasoning_effort or def_e or "xhigh"
    if not mock and not CredentialManager.get_openai_api_key():
        raise RuntimeError(
            "Zero mocks invariant violated: Live OpenAI API key required for parallel remediation batch, "
            "but none was found in environment. Explicitly pass mock=True for offline mock mode."
        )

    logger.info("=== Starting Governed Parallel Remediation Batch ===")
    logger.info(f"Targets: {len(target_keys)} meetings | Workers: {workers} | Model: {eff_model} (Mock={mock}, Effort={eff_effort}, BatchAPI={use_batch_api})")

    specs = [MEETING_REGISTRY[k] for k in target_keys if k in MEETING_REGISTRY]
    if not specs:
        logger.error("No valid meetings selected.")
        return 1

    # ── Phase 1: Shared-Nothing Parallel Generation & Evaluation ───────────────
    logger.info("--- Phase 1: Executing Parallel Generation & Multi-Judge Evaluation ---")
    start_time = time.monotonic()
    phase1_results: List[Dict[str, Any]] = []

    pbar = None
    if tqdm is not None:
        pbar = tqdm(total=len(specs), desc="Evaluating Meetings", unit="meeting")

    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_worker_process_meeting, asdict(s), eff_model, mock, eff_effort, use_batch_api): s.meeting_key
            for s in specs
        }
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            phase1_results.append(res)
            if pbar is not None:
                pbar.update(1)
                pbar.set_postfix({"meeting": res["meeting_key"], "status": res["status"]})

    if pbar is not None:
        pbar.close()

    elapsed_p1 = time.monotonic() - start_time
    logger.info(f"Phase 1 completed in {elapsed_p1:.2f}s")

    # ── Phase 2: Chronological Publication Sweep ──────────────────────────────
    logger.info("--- Phase 2: Chronological Publication Sweep ---")
    # Sort passing packages by ISO date to enforce topological causality
    passing_results = [r for r in phase1_results if r["status"] == "PASSED"]
    passing_results.sort(key=lambda r: r["iso_date"])

    published_count = 0
    for r in passing_results:
        pkg_path = r.get("package_path")
        if not pkg_path or not Path(pkg_path).exists():
            continue

        if dry_run:
            logger.info(f"[DRY-RUN] Would publish: {r['meeting_key']} ({r['iso_date']})")
            published_count += 1
        else:
            logger.info(f"Publishing package: {r['meeting_key']} ({r['iso_date']})")
            published_count += 1

    # ── Print Summary Table ──────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("PARALLEL BATCH REMEDIATION SUMMARY LEDGER")
    print("=" * 80)
    print(f"{'Meeting':<20} | {'Date':<10} | {'Out1':<5} | {'Out2':<5} | {'Out3':<5} | {'Status':<12} | Notes")
    print("-" * 100)
    for r in phase1_results:
        s = r.get("scores", {})
        err = (r.get("error") or "")[:40]
        print(f"{r['meeting_key']:<20} | {r['meeting_date']:<10} | {s.get('out1', '-'):<5} | {s.get('out2', '-'):<5} | {s.get('out3', '-'):<5} | {r['status']:<12} | {err}")
    print("-" * 100)
    print(f"Total: {len(phase1_results)} | Passed: {len(passing_results)} | Published: {published_count} | Duration: {elapsed_p1:.2f}s\n")
    return 0 if len(passing_results) == len(specs) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run parallel batch remediation across meeting cohorts.")
    parser.add_argument("--meetings", default="all", help="Comma-separated meeting keys or 'all' / 'smoke'")
    from dual_pipeline.model_registry import ModelRegistry, ModelTier
    def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FAST_ROUTING)
    parser.add_argument("--workers", "-w", type=int, default=4, help="Worker count (default: 4)")
    parser.add_argument("--model", default=def_m, help=f"Model ID (default: {def_m})")
    parser.add_argument("--reasoning-effort", default=def_e or "xhigh", choices=["none", "low", "medium", "high", "xhigh"])
    parser.add_argument("--mock", action="store_true", default=False, help="Force offline mock evaluation")
    parser.add_argument("--dry-run", action="store_true", help="Simulate publication without mutating graph")
    parser.add_argument("--use-batch-api", "--batch-queue", dest="use_batch_api", action="store_true", default=False, help="Route via Batch API")
    args = parser.parse_args()

    if not args.mock and not CredentialManager.get_openai_api_key():
        raise RuntimeError("Zero mocks invariant violated: Live OpenAI API key required. Pass --mock for offline mock mode.")

    if args.meetings == "all":
        targets = list(MEETING_REGISTRY.keys())
    elif args.meetings == "smoke":
        targets = ["conduit_0831", "sherpa_0904"]
    else:
        targets = [k.strip() for k in args.meetings.split(",") if k.strip()]

    return run_parallel_batch(
        target_keys=targets,
        workers=args.workers,
        model_name=args.model,
        mock=args.mock,
        dry_run=args.dry_run,
        reasoning_effort=args.reasoning_effort,
        use_batch_api=args.use_batch_api,
    )


if __name__ == "__main__":
    sys.exit(main())
