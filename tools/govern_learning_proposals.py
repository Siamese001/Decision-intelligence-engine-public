#!/usr/bin/env python3
"""Governed Learning Change Proposals Management CLI (Layer 6).

Usage:
    python tools/govern_learning_proposals.py list [--proposals-dir <path>]
    python tools/govern_learning_proposals.py inspect <proposal_id_or_path> [--proposals-dir <path>]
    python tools/govern_learning_proposals.py simulate <proposal_id_or_path> [--proposals-dir <path>]
    python tools/govern_learning_proposals.py approve-and-apply <proposal_id_or_path> --receipt <path> [--proposals-dir <path>]
    python tools/govern_learning_proposals.py rollback <proposal_id_or_path> [--proposals-dir <path>]
    python tools/govern_learning_proposals.py hitl-scan [--ledger-path <path>] [--override-threshold <float>] [--min-decisions <int>] [--no-proposals] [--proposals-dir <path>]

Enforces cryptographic approval binding, schema validation, and simulation checks
prior to any mutation of runtime configuration or learned assets.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import read_json, write_json  # noqa: E402
from dual_pipeline.learning_engine import (  # noqa: E402
    ClosedLoopLearningEngine,
    get_learning_engine,
)


_RECEIPT_SUFFIXES = (
    "_simulation_receipt",
    "_applied_receipt",
    "_rollback_receipt",
    "_approval_receipt",
)


def _strip_receipt_suffix(stem: str) -> tuple[str, bool]:
    for suffix in _RECEIPT_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)], True
    return stem, False


def _resolve_proposal_file(proposals_dir: Path, target: str) -> Path:
    target_path = Path(target)
    raw_stem = target_path.stem if target_path.suffix == ".json" else target_path.name
    base_stem, was_receipt = _strip_receipt_suffix(raw_stem)

    if was_receipt:
        for parent in (target_path.parent, proposals_dir):
            base_candidate = parent / f"{base_stem}.json"
            if base_candidate.is_file():
                return base_candidate.resolve()
        raise ValueError(
            f"Target '{target}' is a receipt artifact and parent proposal '{base_stem}.json' does not exist"
        )

    if target_path.is_file():
        return target_path.resolve()

    candidate = proposals_dir / f"{target}.json"
    if candidate.is_file():
        return candidate.resolve()

    candidate_raw = proposals_dir / target
    if candidate_raw.is_file():
        return candidate_raw.resolve()

    raise FileNotFoundError(f"Cannot resolve proposal '{target}' in {proposals_dir}")


def cmd_list(engine: ClosedLoopLearningEngine, proposals_dir: Path) -> int:
    """Lists all pending and generated learning-change-proposal artifacts."""
    files = sorted(proposals_dir.glob("*.json"))
    proposals = []
    for f in files:
        if f.stem.endswith(_RECEIPT_SUFFIXES):
            continue
        try:
            data = read_json(f)
            if data.get("contract_id") == "urn:nee:dual-pipeline:learning-change-proposal:1.0.0":
                proposals.append(data)
        except Exception:
            continue

    if not proposals:
        print(f"No learning change proposals found in {proposals_dir}")
        return 0

    print(f"\nGoverned Learning Change Proposals ({len(proposals)} found):")
    print("-" * 95)
    print(f"{'Proposal ID':<36} {'Type':<18} {'Risk':<8} {'Status':<10} {'Digest':<16}")
    print("-" * 95)
    for p in proposals:
        p_id = p.get("proposal_id", "unknown")[:34]
        p_type = p.get("proposal_type", "unknown")[:16]
        risk = p.get("risk", "unknown")[:6]
        status = p.get("status", "unknown")[:8]
        digest = p.get("canonical_digest", {}).get("value", "unknown")[:14] + "..."
        print(f"{p_id:<36} {p_type:<18} {risk:<8} {status:<10} {digest:<16}")
    print("-" * 95)
    return 0


def cmd_inspect(engine: ClosedLoopLearningEngine, proposals_dir: Path, target: str) -> int:
    """Displays formatted details of a learning change proposal."""
    try:
        path = _resolve_proposal_file(proposals_dir, target)
        data = read_json(path)
    except Exception as e:
        print(f"Error inspecting proposal: {e}", file=sys.stderr)
        return 1

    print("\n================================================================================")
    print(f"  LEARNING CHANGE PROPOSAL: {data.get('proposal_id')}")
    print("================================================================================")
    print(f"Contract ID:        {data.get('contract_id')}")
    print(f"Schema Version:     {data.get('schema_version')}")
    print(f"Proposal Type:      {data.get('proposal_type')}")
    print(f"Status:             {data.get('status')}")
    print(f"Risk Level:         {data.get('risk')}")
    print(f"Approval Required:  {data.get('approval_required')}")
    print(f"Auto Apply:         {data.get('auto_apply')}")
    print(f"Created At:         {data.get('created_at')}")
    print(f"Canonical Digest:   {data.get('canonical_digest', {}).get('value')}")
    print(f"\nProblem Statement:\n  {data.get('problem')}")
    print(f"\nProposed Change:\n  {data.get('proposed_change')}")
    print("\nEvaluation Plan:")
    for step in data.get("evaluation_plan", []):
        print(f"  - {step}")
    print("\nExtensions Payload:")
    print(json.dumps(data.get("extensions", {}), indent=4))
    print("================================================================================\n")
    return 0


def cmd_simulate(engine: ClosedLoopLearningEngine, proposals_dir: Path, target: str) -> int:
    """Executes a dry-run replay simulation on the target proposal."""
    try:
        path = _resolve_proposal_file(proposals_dir, target)
        proposal = read_json(path)
    except Exception as e:
        print(f"Error reading proposal: {e}", file=sys.stderr)
        return 1

    result = engine.simulate_proposal(proposal)
    print(f"\nSimulation Result for {result.proposal_id}:")
    print(f"  Status:    {result.status}")
    print(f"  Success:   {result.success}")
    print(f"  Timestamp: {result.simulated_at}")
    print("  Details:")
    for k, v in result.details.items():
        print(f"    {k}: {v}")

    # Emit LP2 telemetry event
    try:
        from dual_pipeline.core import digest, utc_now
        from dual_pipeline.telemetry_core.constants import ledger_connection
        db_path = engine.runtime_root / "ledger" / "learning.sqlite"
        if not db_path.exists():
            db_path = Path(".local/ledger/learning.sqlite")
        if db_path.exists():
            conn = ledger_connection(db_path)
            p_id = proposal.get("proposal_id", "unknown")
            now_ts = utc_now()
            conn.execute(
                """INSERT OR REPLACE INTO learning_event
                   (event_id, run_id, trace_id, span_id, parent_span_id, use_case, stage,
                    event_type, status, payload_reference, payload_digest, artifact_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"evt-lp2-{p_id}",
                    f"run-learning-{p_id}",
                    f"trace-learn-{p_id}",
                    f"span-lp2-{p_id}",
                    f"span-lp1-{p_id}",
                    "learning",
                    "LP2",
                    "learning_simulation_completed",
                    "PASS" if result.success else "FAIL",
                    str(p_id),
                    digest(result.to_dict()),
                    json.dumps(result.to_dict()),
                    now_ts,
                ),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass

    return 0 if result.success else 1


def cmd_approve_and_apply(
    engine: ClosedLoopLearningEngine,
    proposals_dir: Path,
    target: str,
    receipt_path: Path,
) -> int:
    """Verifies cryptographic approval receipt and applies the proposal."""
    try:
        path = _resolve_proposal_file(proposals_dir, target)
        proposal = read_json(path)
    except Exception as e:
        print(f"Error reading proposal: {e}", file=sys.stderr)
        return 1

    if not receipt_path.is_file():
        print(f"Approval receipt file not found: {receipt_path}", file=sys.stderr)
        return 1

    try:
        approval_receipt = read_json(receipt_path)
    except Exception as e:
        print(f"Error reading approval receipt: {e}", file=sys.stderr)
        return 1

    try:
        p_id = proposal.get("proposal_id", Path(path).stem)
        sim_receipt_path = proposals_dir / f"{p_id}_simulation_receipt.json"
        if not sim_receipt_path.is_file():
            sim_res = engine.simulate_proposal(proposal)
            if not sim_res.success or sim_res.status not in ("SIMULATED_PASS", "PASS"):
                raise PermissionError(
                    f"Cannot apply proposal {p_id}: simulation failed with status '{sim_res.status}'"
                )
        receipt = engine.apply_approved_proposal(proposal, approval_receipt)
        ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.learning", {})
        if ext.get("from_id") and ext.get("to_id"):
            try:
                from dual_pipeline.transcript_metadata_core.speaker_priors_registry import SpeakerPriorsRegistry
                priors_p = Path(ext["priors_path"]) if ext.get("priors_path") else (engine.runtime_root / "learned_speaker_priors.json")
                SpeakerPriorsRegistry(priors_p).register_alias(str(ext["from_id"]), str(ext["to_id"]))
            except Exception:
                pass
        elif ext.get("parameter_name") and ext.get("proposed_value") is not None:
            try:
                from dual_pipeline.transcript_metadata_core.speaker_priors_registry import SpeakerPriorsRegistry
                priors_p = Path(ext["priors_path"]) if ext.get("priors_path") else (engine.runtime_root / "learned_speaker_priors.json")
                SpeakerPriorsRegistry(priors_p).update_thresholds({str(ext["parameter_name"]): float(ext["proposed_value"])})
            except Exception:
                pass
        print("\n✅ Proposal successfully applied under governed approval:")
        print(f"  Receipt ID:    {receipt.get('receipt_id')}")
        print(f"  Proposal ID:   {receipt.get('proposal_id')}")
        print(f"  Approved By:   {receipt.get('applied_by')}")
        print(f"  Applied Change:{receipt.get('applied_change')}")
        print(f"  Applied At:    {receipt.get('applied_at')}")

        # Emit LP3, LP4 and seal learning stage chain if ledger exists
        try:
            from dual_pipeline.learning_loop_core.seal import (
                record_learning_event,
                seal_learning_stage_chain,
            )
            p_id = proposal.get("proposal_id", "unknown")
            run_id = f"run-learning-{p_id}"
            db_path = engine.runtime_root / "ledger" / "learning.sqlite"
            if not db_path.exists():
                db_path = Path(".local/ledger/learning.sqlite")

            record_learning_event(
                stage="LP3",
                event_type="learning_approval_verified",
                status="PASS",
                run_id=run_id,
                proposal_id=p_id,
                payload_reference=approval_receipt.get("receipt_id", "app_receipt"),
                artifact_data=dict(approval_receipt),
                db_path=db_path if db_path.exists() else None,
                parent_span_id=f"span-lp2-{p_id}",
            )
            record_learning_event(
                stage="LP4",
                event_type="learning_commit_applied",
                status="PASS",
                run_id=run_id,
                proposal_id=p_id,
                payload_reference=receipt.get("receipt_id", "apply_receipt"),
                artifact_data=dict(receipt),
                db_path=db_path if db_path.exists() else None,
                parent_span_id=f"span-lp3-{p_id}",
            )
            events_to_seal = []
            if db_path.exists():
                from storage.connection import get_connection
                with get_connection(db_path, readonly=True) as conn:
                    cursor = conn.execute(
                        "SELECT stage, event_type, status, payload_digest, artifact_json FROM learning_event WHERE run_id = ? ORDER BY stage ASC",
                        (run_id,),
                    )
                    for row in cursor.fetchall():
                        events_to_seal.append({
                            "stage": row["stage"],
                            "event_type": row["event_type"],
                            "status": row["status"],
                            "payload_digest": row["payload_digest"],
                        })
            if len(events_to_seal) in (5, 6):
                seal_res = seal_learning_stage_chain(
                    proposal_id=p_id,
                    run_id=run_id,
                    stage_events=events_to_seal,
                    output_dir=engine.runtime_root / "runs" / run_id,
                    require_canary=(len(events_to_seal) == 6),
                )
                print(f"  Stage Seal:    {seal_res.get('merkle_root', '')[:16]}... ({len(events_to_seal)} stages sealed)\n")
        except Exception:
            pass
        return 0
    except PermissionError as pe:
        print(f"\n❌ GOVERNANCE BLOCKED: {pe}\n", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n❌ Execution failure during apply: {e}\n", file=sys.stderr)
        return 1


def cmd_rollback(engine: ClosedLoopLearningEngine, proposals_dir: Path, target: str) -> int:
    """Executes bidirectional rollback of an applied proposal via UniversalWriteGateway."""
    try:
        path = _resolve_proposal_file(proposals_dir, target)
        p_id = Path(path).stem
        rollback_receipt_file = proposals_dir / f"{p_id}_rollback_receipt.json"

        rollback_snapshot_file = proposals_dir / f"{p_id}_rollback_snapshot.json"
        if rollback_snapshot_file.is_file():
            res = engine.rollback_proposal(p_id)
            print(f"✅ Proposal {p_id} rolled back via engine.rollback_proposal (status: {res.get('status')}).")
            return 0

        if rollback_receipt_file.is_file():
            from dual_pipeline.uwg_core.gateway import UniversalWriteGateway
            seed_graph = engine.runtime_root / "seed_graph.sqlite"
            uwg = UniversalWriteGateway(engine.runtime_root, seed_graph)
            res = uwg.process_commit_request({
                "request_type": "learning_parameter_rollback",
                "rollback_path": rollback_receipt_file,
            })
            print(f"✅ Proposal {p_id} rolled back via UniversalWriteGateway (status: {res.get('status')}).")
            return 0

        # Legacy fallback if no rollback receipt
        applied_receipt_file = proposals_dir / f"{p_id}_applied_receipt.json"
        if not applied_receipt_file.is_file():
            print(f"No active receipt found for {p_id}; cannot rollback.", file=sys.stderr)
            return 1

        applied_data = read_json(applied_receipt_file)
        applied_data["status"] = "ROLLED_BACK"
        applied_data["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        write_json(applied_receipt_file, applied_data)
        print(f"✅ Proposal {p_id} applied status updated to ROLLED_BACK.")
        return 0
    except Exception as e:
        print(f"Error rolling back proposal: {e}", file=sys.stderr)
        return 1


def cmd_hitl_scan(
    engine: ClosedLoopLearningEngine,
    proposals_dir: Path,
    ledger_path: Optional[Path] = None,
    override_threshold: float = 0.20,
    min_decisions: int = 5,
    generate_proposals: bool = True,
) -> int:
    """Scans HITL decision ledger for category override patterns and synthesizes learning proposals."""
    from dual_pipeline.hitl_governance_core.ledger import _find_default_ledger_path

    target_ledger = ledger_path.resolve() if ledger_path else _find_default_ledger_path().resolve()
    sqlite_target = target_ledger if target_ledger.suffix == ".sqlite" else target_ledger.with_suffix(".sqlite")
    if not target_ledger.is_file() and not sqlite_target.is_file():
        print(f"HITL decision ledger not found at {target_ledger} or {sqlite_target}. Scan skipped.")
        return 0
    if not target_ledger.is_file() and sqlite_target.is_file():
        target_ledger = sqlite_target

    decisions = engine.ingest_hitl_decisions(target_ledger)
    if not decisions:
        print(f"No valid decisions found in {target_ledger}. Scan complete.")
        return 0

    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for d in decisions:
        cat = str(d.get("category", "default")).strip() or "default"
        by_category.setdefault(cat, []).append(d)

    print("\n================================================================================")
    print(f"  HITL DECISION LEDGER OVERRIDE SCAN ({len(decisions)} total decisions)")
    print(f"  Source: {target_ledger}")
    print("================================================================================")
    print(f"{'Category':<28} {'Total':<8} {'Affirmed':<10} {'Overridden':<12} {'Override %':<12} {'Brier':<8} {'Status':<16}")
    print("-" * 96)

    proposals_created = 0
    for cat, items in sorted(by_category.items()):
        total = len(items)
        overridden = sum(1 for i in items if i.get("outcome_type") == "OVERRIDDEN")
        affirmed = sum(
            1
            for i in items
            if i.get("outcome_type") in ("AFFIRMED", None, "")
        )
        human_evaluated = overridden + affirmed
        rate = overridden / human_evaluated if human_evaluated > 0 else 0.0
        rate_pct = f"{rate * 100:.1f}%"

        sq_errs = []
        for i in items:
            y = 1.0 if i.get("outcome_type") == "AFFIRMED" else 0.0
            p = float(i.get("confidence", 0.70))
            sq_errs.append((p - y) ** 2)
        cat_brier = sum(sq_errs) / total if total > 0 else 0.25
        brier_str = f"{cat_brier:.4f}"

        is_cohort = (rate >= override_threshold) and (total >= min_decisions)
        status = "COHORT DETECTED" if is_cohort else "NORMAL"
        print(f"{cat:<28} {total:<8} {affirmed:<10} {overridden:<12} {rate_pct:<12} {brier_str:<8} {status:<16}")

        if is_cohort and generate_proposals:
            cohort_data = {
                "cohort_key": f"hitl_override:{cat}",
                "occurrence_count": overridden,
                "run_ids": sorted(list({i.get("run_id", "run-hitl") for i in items})) or ["run-hitl-override"],
                "items": items,
            }
            prop = engine.generate_proposal_for_cohort(
                cohort_data,
                proposal_type="hitl_confidence",
                target_override={
                    "category": cat,
                },
            )
            if proposals_dir and proposals_dir.resolve() != engine.proposals_dir.resolve():
                p_id = prop.get("proposal_id", f"prop-{cat}")
                write_json(proposals_dir / f"{p_id}.json", prop)
            proposals_created += 1
            print(f"  --> Generated proposal: {prop.get('proposal_id')} for category '{cat}'")

            # Emit LP0 and LP1 telemetry events if learning database exists
            try:
                from dual_pipeline.core import digest, utc_now
                from dual_pipeline.telemetry_core.constants import ledger_connection
                db_path = engine.runtime_root / "ledger" / "learning.sqlite"
                if not db_path.exists():
                    db_path = Path(".local/ledger/learning.sqlite")
                if db_path.exists():
                    conn = ledger_connection(db_path)
                    now_ts = utc_now()
                    p_id = prop.get("proposal_id", f"prop-{cat}")
                    run_id = f"run-learning-{p_id}"
                    ev_lp0 = {
                        "event_id": f"evt-lp0-{cat}",
                        "run_id": run_id,
                        "trace_id": f"trace-learn-{cat}",
                        "span_id": f"span-lp0-{cat}",
                        "parent_span_id": None,
                        "use_case": "learning",
                        "stage": "LP0",
                        "event_type": "hitl_override_cohort_detected",
                        "status": "PASS",
                        "payload_reference": str(cohort_data["cohort_key"]),
                        "payload_digest": digest(cohort_data),
                        "artifact_json": json.dumps({"cohort_key": cohort_data["cohort_key"], "occurrence_count": overridden}),
                        "created_at": now_ts,
                    }
                    ev_lp1 = {
                        "event_id": f"evt-lp1-{cat}",
                        "run_id": run_id,
                        "trace_id": f"trace-learn-{cat}",
                        "span_id": f"span-lp1-{cat}",
                        "parent_span_id": f"span-lp0-{cat}",
                        "use_case": "learning",
                        "stage": "LP1",
                        "event_type": "learning_proposal_synthesized",
                        "status": "PASS",
                        "payload_reference": str(p_id),
                        "payload_digest": prop.get("canonical_digest", {}).get("value", digest(prop)),
                        "artifact_json": json.dumps({"proposal_id": p_id, "proposal_type": prop.get("proposal_type")}),
                        "created_at": now_ts,
                    }
                    for ev in (ev_lp0, ev_lp1):
                        conn.execute(
                            """INSERT OR REPLACE INTO learning_event
                               (event_id, run_id, trace_id, span_id, parent_span_id, use_case, stage,
                                event_type, status, payload_reference, payload_digest, artifact_json, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                ev["event_id"], ev["run_id"], ev["trace_id"], ev["span_id"],
                                ev.get("parent_span_id"), ev["use_case"], ev["stage"],
                                ev["event_type"], ev["status"], ev["payload_reference"],
                                ev["payload_digest"], ev["artifact_json"], ev["created_at"],
                            ),
                        )
                    conn.commit()
                    conn.close()
            except Exception:
                pass

    print("=" * 96)
    if generate_proposals:
        try:
            from dual_pipeline.learning_engine import DEFAULT_RUNTIME_ROOT
            out_store = (
                (engine.runtime_root / "learned_hitl_option_store.json")
                if engine.runtime_root.resolve() != DEFAULT_RUNTIME_ROOT.resolve()
                else None
            )
            store_out = engine.synthesize_hitl_option_nuance_and_posteriors(
                hitl_ledger_path=target_ledger,
                output_store_path=out_store,
            )
            print(f"✅ Updated learned HITL option nuance store ({store_out.get('total_learned_options', 0)} option pattern(s) calibrated).")
        except Exception as exc:
            print(f"⚠️ Note: Could not synthesize learned HITL option store: {exc}")
    if proposals_created > 0:
        print(f"\n✅ Synthesized {proposals_created} learning-change-proposal:1.0.0 artifact(s) in {proposals_dir}\n")
    else:
        print(f"\nNo override cohorts exceeded threshold ({override_threshold * 100:.0f}% across >= {min_decisions} decisions).\n")
    return 0


def cmd_answer_scan(
    engine: ClosedLoopLearningEngine,
    proposals_dir: Path,
    eval_dir: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
    min_failures: int = 2,
    generate_proposals: bool = True,
) -> int:
    """Scans answer evaluation receipts and feedback for failure cohorts and synthesizes proposals."""
    from dual_pipeline.learning_loop_core import (
        record_learning_event,
        scan_answer_eval_receipts,
        synthesize_answer_learning_proposal,
    )

    target_eval_dir = (eval_dir or (ROOT / "reports" / "answer_eval")).resolve()
    if not target_eval_dir.exists():
        target_eval_dir = (ROOT / "reports").resolve()

    target_ledger = ledger_path.resolve() if ledger_path else None
    cohorts = scan_answer_eval_receipts(
        eval_dir=target_eval_dir,
        ledger_path=target_ledger,
        min_occurrences=min_failures,
    )

    print("\n================================================================================")
    print(f"  ANSWER EVALUATION & FEEDBACK FAILURE SCAN ({len(cohorts)} cohort(s) detected)")
    print(f"  Source Eval Dir: {target_eval_dir}")
    if target_ledger:
        print(f"  Source Ledger:   {target_ledger}")
    print("================================================================================")
    print(f"{'Category':<24} {'Cohort Key':<36} {'Failures':<10} {'Target Parameter':<28}")
    print("-" * 100)

    if not cohorts:
        print(f"No failure cohorts exceeded threshold (>= {min_failures} occurrences).")
        print("=" * 100 + "\n")
        return 0

    proposals_created = 0
    for cohort in cohorts:
        print(f"{cohort.category:<24} {cohort.cohort_key[:34]:<36} {cohort.occurrence_count:<10} {cohort.target_parameter:<28}")
        if generate_proposals:
            prop = synthesize_answer_learning_proposal(cohort, proposals_dir=proposals_dir)
            proposals_created += 1
            p_id = prop.get("proposal_id", "unknown")
            print(f"  --> Synthesized proposal: {p_id} for {cohort.target_parameter} -> {cohort.proposed_value}")

            db_path = engine.runtime_root / "ledger" / "learning.sqlite"
            if not db_path.exists():
                db_path = Path(".local/ledger/learning.sqlite")
            run_id = f"run-learning-{p_id}"
            record_learning_event(
                stage="LP0",
                event_type="answer_eval_cohort_detected",
                status="PASS",
                run_id=run_id,
                proposal_id=p_id,
                payload_reference=cohort.cohort_key,
                artifact_data=cohort.to_dict(),
                db_path=db_path if db_path.exists() else None,
            )
            record_learning_event(
                stage="LP1",
                event_type="learning_proposal_synthesized",
                status="PASS",
                run_id=run_id,
                proposal_id=p_id,
                payload_reference=p_id,
                artifact_data=prop,
                db_path=db_path if db_path.exists() else None,
                parent_span_id=f"span-lp0-{p_id}",
            )

    print("=" * 100)
    print(f"\n✅ Synthesized {proposals_created} learning-change-proposal:1.0.0 artifact(s) in {proposals_dir}\n")
    return 0


def cmd_tokenomics_scan(
    engine: ClosedLoopLearningEngine,
    proposals_dir: Path,
    min_occurrences: int = 2,
    generate_proposals: bool = True,
) -> int:
    """Scan tokenomics signals and synthesize governed learning proposals."""
    signals = list(engine.ingest_telemetry_failures()) + list(engine.ingest_tokenomics_signals())
    cohorts = engine.analyze_failure_cohorts(signals, min_occurrences=min_occurrences)
    created = 0
    if generate_proposals:
        for cohort in cohorts:
            engine.generate_proposal_for_cohort(cohort)
            created += 1
    print(f"[TOKENOMICS-SCAN] Cohorts={len(cohorts)} Proposals={created}")
    return 0


def cmd_healing_scan(
    engine: ClosedLoopLearningEngine,
    proposals_dir: Path,
    db_path: Optional[Path] = None,
    min_occurrences: int = 2,
    generate_proposals: bool = True,
    apply_recalibration: bool = False,
) -> int:
    """Scan persistent healing decisions in SQLite and synthesize governed offline learning proposals."""
    res = engine.run_healing_learning_cycle(
        db_path=db_path,
        min_occurrences=min_occurrences,
        generate_proposals=generate_proposals,
    )
    if apply_recalibration:
        engine.recalibrate_post_learning_confidences(healing_db_path=db_path)
    print(
        f"[HEALING-SCAN] Signals={res['signals_count']} Cohorts={len(res['cohorts'])} "
        f"Proposals={len(res['proposals'])} Posteriors={len(res['posteriors'])}"
    )
    return 0


def cmd_audio_scan(
    engine: ClosedLoopLearningEngine,
    min_occurrences: int = 3,
    min_distinct_sources: int = 2,
) -> int:
    """Scan audio/voiceprint learning events and synthesize governed proposals."""
    res = engine.run_audio_voice_learning_cycle(
        min_occurrences=min_occurrences,
        min_distinct_sources=min_distinct_sources,
    )
    print(
        f"[AUDIO-SCAN] Events={res['events_analyzed']} Cohorts={len(res['cohorts'])} "
        f"Proposals={len(res['proposals'])}"
    )
    return 0


def cmd_corrections_scan(
    engine: ClosedLoopLearningEngine,
    proposals_dir: Path,
    min_cohort_size: int = 3,
) -> int:
    """Scan operator correction feedback events and synthesize governed learning proposals."""
    from dual_pipeline.learning_adapters.correction_feedback_ingestor import CorrectionFeedbackIngestor

    ingestor = CorrectionFeedbackIngestor(
        runtime_root=engine.runtime_root,
        min_confirmations=min_cohort_size,
    )
    proposals, _ = ingestor.synthesize_proposals(min_confirmations=min_cohort_size)
    proposals_dir.mkdir(parents=True, exist_ok=True)
    for prop in proposals:
        write_json(proposals_dir / f"{prop['proposal_id']}.json", prop)
    print(f"[CORRECTIONS-SCAN] Proposals={len(proposals)}")
    return 0


def cmd_unified_cycle(
    engine: ClosedLoopLearningEngine,
    min_occurrences: int = 2,
) -> int:
    """Execute the 8-surface unified closed-loop learning cycle."""
    res = engine.run_unified_learning_cycle(min_occurrences=min_occurrences)
    print(
        f"[UNIFIED-CYCLE] Surfaces={res['surfaces_orchestrated']} "
        f"Proposals={res['total_proposals_generated']}"
    )
    return 0


def _register_extended_subparsers(subparsers: Any, parent_parser: argparse.ArgumentParser) -> None:
    audio_p = subparsers.add_parser(
        "audio-scan",
        parents=[parent_parser],
        help="Scan acoustic/voiceprint learning events and synthesize proposals",
    )
    audio_p.add_argument("--storage-dir", type=Path, default=None, help="Runtime storage directory")
    audio_p.add_argument("--min-occurrences", type=int, default=3)
    audio_p.add_argument("--min-distinct-sources", type=int, default=2)

    corr_p = subparsers.add_parser(
        "corrections-scan",
        parents=[parent_parser],
        help="Scan operator correction feedback and synthesize proposals",
    )
    corr_p.add_argument("--storage-dir", type=Path, default=None, help="Runtime storage directory")
    corr_p.add_argument("--min-cohort-size", type=int, default=3)

    unif_p = subparsers.add_parser(
        "unified-cycle",
        parents=[parent_parser],
        help="Orchestrate all 8 system learning surfaces in a single deterministic pass",
    )
    unif_p.add_argument("--storage-dir", type=Path, default=None, help="Runtime storage directory")
    unif_p.add_argument("--min-occurrences", type=int, default=2)


def _dispatch_extended_command(args: argparse.Namespace, engine: ClosedLoopLearningEngine) -> Optional[int]:
    eff_engine = (
        ClosedLoopLearningEngine(storage_dir=args.storage_dir, proposals_dir=args.proposals_dir)
        if getattr(args, "storage_dir", None)
        else engine
    )
    if args.command == "audio-scan":
        return cmd_audio_scan(
            eff_engine,
            min_occurrences=args.min_occurrences,
            min_distinct_sources=args.min_distinct_sources,
        )
    if args.command == "corrections-scan":
        return cmd_corrections_scan(
            eff_engine,
            args.proposals_dir,
            min_cohort_size=args.min_cohort_size,
        )
    if args.command == "unified-cycle":
        return cmd_unified_cycle(eff_engine, min_occurrences=args.min_occurrences)
    return None


def main() -> int:
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--proposals-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Directory containing learning-change-proposal JSON artifacts",
    )
    parser = argparse.ArgumentParser(
        description="Governed Learning Change Proposals Management CLI (Layer 6)",
        parents=[parent_parser],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    subparsers.add_parser("list", parents=[parent_parser], help="List available learning proposals")

    # inspect
    inspect_p = subparsers.add_parser("inspect", parents=[parent_parser], help="Inspect a proposal payload")
    inspect_p.add_argument("target", type=str, help="Proposal ID or JSON path")

    # simulate
    sim_p = subparsers.add_parser("simulate", parents=[parent_parser], help="Run dry-run simulation on a proposal")
    sim_p.add_argument("target", type=str, help="Proposal ID or JSON path")

    # approve-and-apply
    apply_p = subparsers.add_parser("approve-and-apply", parents=[parent_parser], help="Approve and apply a proposal")
    apply_p.add_argument("target", type=str, help="Proposal ID or JSON path")
    apply_p.add_argument("--receipt", type=Path, required=True, help="Path to approval receipt JSON")

    # rollback
    rb_p = subparsers.add_parser("rollback", parents=[parent_parser], help="Rollback an applied proposal")
    rb_p.add_argument("target", type=str, help="Proposal ID or JSON path")

    # hitl-scan
    hitl_p = subparsers.add_parser("hitl-scan", parents=[parent_parser], help="Scan HITL decision ledger for override patterns")
    hitl_p.add_argument("--ledger-path", type=Path, default=None, help="Path to hitl_decision_ledger.json")
    hitl_p.add_argument("--override-threshold", type=float, default=0.20, help="Override rate threshold (default: 0.20)")
    hitl_p.add_argument("--min-decisions", type=int, default=5, help="Minimum decisions in category (default: 5)")
    hitl_p.add_argument("--no-proposals", action="store_true", help="Report only without generating proposals")

    # answer-scan
    ans_p = subparsers.add_parser("answer-scan", parents=[parent_parser], help="Scan answer evaluation receipts for failure cohorts")
    ans_p.add_argument("--eval-dir", type=Path, default=None, help="Directory containing answer evaluation receipts")
    ans_p.add_argument("--ledger-path", type=Path, default=None, help="Path to answer ledger JSON or SQLite")
    ans_p.add_argument("--min-failures", type=int, default=2, help="Minimum failures in category (default: 2)")
    ans_p.add_argument("--no-proposals", action="store_true", help="Report only without generating proposals")

    tok_p = subparsers.add_parser(
        "tokenomics-scan",
        parents=[parent_parser],
        help="Scan tokenomics telemetry/judge/healer cohorts and synthesize proposals",
    )
    tok_p.add_argument("--min-occurrences", type=int, default=2)
    tok_p.add_argument("--no-proposals", action="store_true")

    heal_p = subparsers.add_parser(
        "healing-scan",
        parents=[parent_parser],
        help="Scan persistent healing decisions store and synthesize offline learning proposals",
    )
    heal_p.add_argument("--db-path", type=Path, default=None, help="Path to learning.sqlite")
    heal_p.add_argument("--min-occurrences", type=int, default=2)
    heal_p.add_argument("--no-proposals", action="store_true")
    heal_p.add_argument("--apply-recalibration", action="store_true", help="Explicitly run post-learning confidence recalibration")

    canary_p = subparsers.add_parser(
        "verify-canary",
        parents=[parent_parser],
        help="Verify post-apply LP5 canary metrics and seal LP0-LP5 or auto-rollback",
    )
    canary_p.add_argument("--proposal-id", type=str, required=True, help="Proposal ID")
    canary_p.add_argument("--baseline-mrr", type=float, required=True, help="Baseline MRR")
    canary_p.add_argument("--canary-mrr", type=float, required=True, help="Canary MRR")
    canary_p.add_argument("--baseline-ndcg", type=float, default=None, help="Baseline NDCG")
    canary_p.add_argument("--canary-ndcg", type=float, default=None, help="Canary NDCG")
    canary_p.add_argument("--storage-dir", type=Path, default=None, help="Runtime storage directory")

    fleet_p = subparsers.add_parser(
        "reconcile-fleet",
        parents=[parent_parser],
        help="Reconcile cross-agent override cohorts and emit parameter_learning_proposals.json",
    )
    fleet_p.add_argument("--ledger-path", type=Path, default=None, help="Path to hitl_decision_ledger.json")
    fleet_p.add_argument("--output-receipt", type=Path, default=None, help="Output receipt JSON path")
    fleet_p.add_argument("--min-occurrences", type=int, default=2, help="Min occurrences per cohort")
    _register_extended_subparsers(subparsers, parent_parser)

    args = parser.parse_args()
    explicit_proposals_dir = getattr(args, "proposals_dir", None)
    storage_dir_arg = getattr(args, "storage_dir", None)
    if explicit_proposals_dir is None:
        args.proposals_dir = (
            (Path(storage_dir_arg) / "proposals")
            if storage_dir_arg is not None
            else (ROOT / "reports" / "learning_proposals")
        )
    engine = get_learning_engine(
        runtime_root=storage_dir_arg,
        proposals_dir=args.proposals_dir,
    )
    ext_rc = _dispatch_extended_command(args, engine)
    if ext_rc is not None:
        return ext_rc

    if args.command == "list":
        return cmd_list(engine, args.proposals_dir)
    elif args.command == "inspect":
        return cmd_inspect(engine, args.proposals_dir, args.target)
    elif args.command == "simulate":
        return cmd_simulate(engine, args.proposals_dir, args.target)
    elif args.command == "approve-and-apply":
        return cmd_approve_and_apply(engine, args.proposals_dir, args.target, args.receipt)
    elif args.command == "rollback":
        return cmd_rollback(engine, args.proposals_dir, args.target)
    elif args.command == "hitl-scan":
        return cmd_hitl_scan(
            engine,
            args.proposals_dir,
            ledger_path=args.ledger_path,
            override_threshold=args.override_threshold,
            min_decisions=args.min_decisions,
            generate_proposals=not args.no_proposals,
        )
    elif args.command == "answer-scan":
        return cmd_answer_scan(
            engine,
            args.proposals_dir,
            eval_dir=args.eval_dir,
            ledger_path=args.ledger_path,
            min_failures=args.min_failures,
            generate_proposals=not args.no_proposals,
        )
    elif args.command == "tokenomics-scan":
        return cmd_tokenomics_scan(
            engine,
            args.proposals_dir,
            min_occurrences=args.min_occurrences,
            generate_proposals=not args.no_proposals,
        )
    elif args.command == "healing-scan":
        return cmd_healing_scan(
            engine,
            args.proposals_dir,
            db_path=args.db_path,
            min_occurrences=args.min_occurrences,
            generate_proposals=not args.no_proposals,
            apply_recalibration=bool(getattr(args, "apply_recalibration", False)),
        )
    elif args.command == "verify-canary":
        from dual_pipeline.learning_engine import verify_post_apply_canary_and_seal
        c_engine = (
            ClosedLoopLearningEngine(storage_dir=args.storage_dir, proposals_dir=args.proposals_dir)
            if args.storage_dir
            else engine
        )
        base_m = {"mrr": args.baseline_mrr}
        can_m = {"mrr": args.canary_mrr}
        if args.baseline_ndcg is not None:
            base_m["ndcg"] = args.baseline_ndcg
        if args.canary_ndcg is not None:
            can_m["ndcg"] = args.canary_ndcg
        res = verify_post_apply_canary_and_seal(
            c_engine,
            proposal_id=args.proposal_id,
            baseline_metrics=base_m,
            canary_metrics=can_m,
        )
        print(f"[VERIFY-CANARY] Proposal={args.proposal_id} Status={res['status']}")
        return 0 if res["status"] in ("SEALED", "ROLLED_BACK") else 1
    elif args.command == "reconcile-fleet":
        res = engine.reconcile_cross_agent_learning_fleet(
            hitl_ledger_path=args.ledger_path,
            output_receipt_path=args.output_receipt,
            min_occurrences=args.min_occurrences,
        )
        print(
            f"[RECONCILE-FLEET] Signals={res['total_signals_evaluated']} "
            f"Cohorts={res['cohorts_detected']} Proposals={res['proposals_synthesized']}"
        )
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

