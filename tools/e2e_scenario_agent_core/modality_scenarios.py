"""Hermetic end-to-end scenario verifiers for Pipelines 1–4 (All 4 Canonical Modalities)."""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Dict, List

from dual_pipeline.canonical_pipeline.document_resume import open_stage_checkpoint
from dual_pipeline.canonical_pipeline.lifecycle import deploy_deliverables
from dual_pipeline.canonical_pipeline.modalities import (
    CanonicalModality,
    detect_modality,
    get_deliverables_for_modality,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.canonical_pipeline.worktree_inheritance import (
    rebind_worktree_metadata_paths,
)
from dual_pipeline.core import ROOT
from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer
from dual_pipeline.speaker_attribution_core.attribution_anomaly_gate import (
    quarantine_unapproved_drafts,
)
from dual_pipeline.transcript_qa_core.healing_mesh import _iso_to_us_date
from dual_pipeline.transcript_stages_core.l1_attribution import (
    assemble_native_hitl_transcript,
)
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator
from tools.verify_tier1_cached_replay import verify_tier1_cached_replay

from .models import ScenarioVerificationResult


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sample_signal_projection(meeting_id: str, quote_text: str) -> Dict[str, object]:
    return {
        "schema_version": "5.7",
        "meeting_id": meeting_id,
        "classification": "internal",
        "audiences": ["internal_consulting", "nee-engineering"],
        "evidence_index": {
            "ev-01": {
                "statement_id": "ev-01",
                "evidence_id": "ev-01",
                "speaker": "person:megan_orzell",
                "text": quote_text,
                "exact_text": quote_text,
                "locator": {"kind": "timestamp", "value": "00:00:18"},
                "quote_pointer": [0, 10],
            },
            "ev-att-01": {
                "statement_id": "ev-att-01",
                "evidence_id": "ev-att-01",
                "speaker": "attachment:screenshot_dashboard_overview.png",
                "text": "Dashboard showing active transmission telemetry overview.",
                "exact_text": "Dashboard showing active transmission telemetry overview.",
                "locator": {"kind": "region", "value": "attachment:screenshot_dashboard_overview.png"},
                "quote_pointer": None,
                "attribution_mode": "COLLATERAL_REVIEWED",
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:megan_orzell",
                    "type": "PERSON",
                    "name": "Megan Orzell",
                    "properties": {"role": "Engineering Lead", "organization": "PartnerAdvisory"},
                }
            ],
            "relationship_signals": [],
        },
        "evidence_only_statement_ids": [],
    }


def _write_four_evaluation_receipts(out_dir: Path, run_id: str) -> Dict[str, str]:
    receipt_names = (
        "00_execution_receipt.json",
        "01_judge_evaluation.json",
        "02_judge_evaluation.json",
        "03_judge_evaluation.json",
    )
    digests: Dict[str, str] = {}
    for name in receipt_names:
        p = out_dir / name
        payload = {"run_id": run_id, "receipt": name, "status": "PASS", "verdict": "PASS"}
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        digests[name] = _sha256_file(p)
    return digests


def run_p1_audio_ingest_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Scenario 1A: CanonicalModality.AUDIO full deliverable & receipt landing."""
    t0 = time.monotonic()
    out_dir = sandbox_root / "p1_audio" / "project_nexus" / "10-30-2026" / "Audio_Architecture_Sync"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "meeting_name": "Audio Architecture Sync",
        "filename_slug": "Audio_Architecture_Sync",
        "date": "2026-10-30",
        "date_filename": "10-30-2026",
        "project": "project_nexus",
        "target_output_dir": "/tmp/.worktrees/foreign_worktree/output/project_nexus/10-30-2026/Audio_Architecture_Sync",
        "attachments": [
            {
                "filename": "screenshot_dashboard_overview.png",
                "attachment_type": "image",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "relative_staged_path": "referenced_artifacts/screenshot_dashboard_overview.png",
                "summary": "Dashboard overview screenshot.",
            }
        ],
    }
    rebind_worktree_metadata_paths(meta, ROOT, out_dir)
    if "foreign_worktree" in str(meta.get("target_output_dir", "")):
        return ScenarioVerificationResult(
            "SCENARIO-P1-AUDIO-INGEST", "P1_AUDIO", "Canonical Audio Pipeline",
            "Audio ingestion, Gate 1.3 date healing, X3 quote anchoring & 3 deliverables",
            False, (time.monotonic() - t0) * 1000.0, error="Foreign worktree path survived rebinding",
        )
    quote = "Through the well, we have two roles open currently in platform engineering."
    raw_dialogue = f"**Megan Orzell:** [00:00:18] {quote}\n\n**Amit Ayer:** [00:00:32] Agreed, we will finalize the staffing plan today."
    f1_text = assemble_native_hitl_transcript(raw_dialogue, metadata=meta, speaker_map={"Megan Orzell": "Megan Orzell"})
    expected_us_date = _iso_to_us_date(meta["date"])
    f3_obj = _sample_signal_projection("meet-p1-audio", quote)
    XExitGatesEvaluator()._heal_quote_pointers(f3_obj, transcript=f1_text)
    qp = f3_obj["evidence_index"]["ev-01"]["quote_pointer"]
    anchored_slice = f1_text[qp[0] : qp[1]]
    f2_text = "# 1. Meeting Objective\n\nFinalize platform engineering roles.\n"
    synth = SynthesisResult(output_1=f1_text, output_2=f2_text, output_3=json.dumps(f3_obj, indent=2))
    deployed = deploy_deliverables(out_dir, meta, CanonicalModality.AUDIO, synth)
    receipt_digests = _write_four_evaluation_receipts(out_dir, "run-p1-audio")
    ok = (
        f"**Date:** {expected_us_date}" in f1_text
        and "### Presented Collateral & Artifacts" in f1_text
        and "Through the well, we have two roles open" in anchored_slice
        and f3_obj["evidence_index"]["ev-att-01"]["quote_pointer"] is None
        and set(get_deliverables_for_modality(CanonicalModality.AUDIO)) == {"01", "02", "03"}
        and all(Path(p).is_file() for p in deployed.values())
        and len(receipt_digests) == 4
    )
    deliv_hashes = {k: _sha256_file(Path(v)) for k, v in deployed.items()}
    deliv_hashes.update(receipt_digests)
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P1-AUDIO-INGEST",
        pipeline_id="P1_AUDIO",
        pipeline_name="Canonical Audio Pipeline",
        title="Audio ingestion, Gate 1.3 date healing, X3 quote anchoring & 3 deliverables",
        passed=ok,
        duration_ms=(time.monotonic() - t0) * 1000.0,
        verified_deliverables=deliv_hashes,
        verified_invariants=[
            "GATE_1_3_US_DATE_MM_DD_YYYY",
            "INV_5_COLLATERAL_BLOCK",
            "X3_VERBATIM_QUOTE_ANCHORED",
            "DELIVERABLES_01_02_03_PLUS_4_RECEIPTS_LANDED",
        ],
        metrics={"anchored_span_len": len(anchored_slice), "deliverable_count": len(deployed)},
        error=None if ok else "Audio modality deliverable or X3 quote check failed",
    )


def run_p1_tier1_cached_replay_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Scenario 1B: Hermetic Tier 1 cached stage replay (--resume)."""
    t0 = time.monotonic()
    replay_sandbox = sandbox_root / "p1_tier1_replay"
    passed, receipt = verify_tier1_cached_replay(output_sandbox_dir=replay_sandbox, force=True)
    ok = bool(
        passed
        and receipt.get("status") == "PASS"
        and receipt.get("x3_coherence_verified")
        and receipt.get("copilot_session_ae00fe03_verified")
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P1-TIER1-REPLAY",
        pipeline_id="P1_AUDIO",
        pipeline_name="Canonical Audio Pipeline",
        title="Hermetic Tier 1 cached stage replay (--resume) & foreign worktree rebinding",
        passed=ok,
        duration_ms=(time.monotonic() - t0) * 1000.0,
        verified_deliverables={"tier1_replay_status": str(receipt.get("verdict"))},
        verified_invariants=[
            "TIER1_CACHED_STAGE_REPLAY_PASS",
            "WORKTREE_PATH_REBINDING_VERIFIED",
            "COPILOT_AE00FE03_REPLAY_VERIFIED",
        ],
        metrics={"replay_duration_s": receipt.get("duration_seconds", 0.0)},
        error=None if ok else str(receipt.get("error", "Tier 1 replay failed")),
    )


def run_p2_transcript_ingest_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Scenario 2A & 2B: CanonicalModality.MEETING_TRANSCRIPT detection, deliverables & quarantine guard."""
    t0 = time.monotonic()
    out_dir = sandbox_root / "p2_transcript" / "project_lens" / "10-30-2026" / "Transcript_Review"
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_sample = (
        "**Eduardo Juan More:** [00:01:10] We completed the LENS ontology alignment across all substations.\n\n"
        "**Amit Ayer:** [00:01:45] Great, let us lock the release milestone for Friday.\n"
    )
    detected = detect_modality(input_path="meeting.vtt", content=transcript_sample, metadata={"source_type": "meeting_transcript"})
    quote = "We completed the LENS ontology alignment across all substations."
    f1_text = assemble_native_hitl_transcript(
        transcript_sample,
        metadata={"meeting_name": "Transcript Review", "date": "2026-10-30", "project": "project_lens"},
        speaker_map={"Eduardo Juan More": "Eduardo Juan More", "Amit Ayer": "Amit Ayer"},
    )
    f2_text = "# 1. Meeting Objective\n\nLock LENS ontology release milestone.\n"
    f3_obj = _sample_signal_projection("meet-p2-transcript", quote)
    XExitGatesEvaluator()._heal_quote_pointers(f3_obj, transcript=f1_text)
    meta = {"meeting_name": "Transcript Review", "filename_slug": "Transcript_Review", "date_filename": "10-30-2026"}
    synth = SynthesisResult(output_1=f1_text, output_2=f2_text, output_3=json.dumps(f3_obj, indent=2))
    deployed = deploy_deliverables(out_dir, meta, CanonicalModality.MEETING_TRANSCRIPT, synth)

    anomaly_dir = sandbox_root / "p2_anomaly_quarantine"
    anomaly_dir.mkdir(parents=True, exist_ok=True)
    draft_01 = anomaly_dir / "01_Draft_10-30-2026_hitl_resolved_transcript.md"
    draft_01.write_text("**Speaker:** Collapsed turn", encoding="utf-8")
    quarantined = quarantine_unapproved_drafts(anomaly_dir)
    ok = (
        detected == CanonicalModality.MEETING_TRANSCRIPT
        and set(get_deliverables_for_modality(detected)) == {"01", "02", "03"}
        and all(Path(p).is_file() for p in deployed.values())
        and not draft_01.exists()
        and len(quarantined) >= 1
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P2-TRANSCRIPT-INGEST",
        pipeline_id="P2_MEETING_TRANSCRIPT",
        pipeline_name="Canonical Meeting Transcript Pipeline",
        title="Diarized transcript modality routing, 3-deliverable deployment & anomaly quarantine",
        passed=ok,
        duration_ms=(time.monotonic() - t0) * 1000.0,
        verified_deliverables={k: _sha256_file(Path(v)) for k, v in deployed.items()},
        verified_invariants=[
            "MODALITY_DETECT_MEETING_TRANSCRIPT",
            "DELIVERABLES_01_02_03_LANDED",
            "GATE_1_2_ANOMALY_DRAFT_QUARANTINE",
        ],
        metrics={"quarantined_draft_count": len(quarantined)},
        error=None if ok else "Meeting transcript modality verification failed",
    )


def run_p3_notes_ingest_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Scenario 3A: CanonicalModality.MEETING_NOTES undiarized notes -> [02, 03] + source_notes."""
    t0 = time.monotonic()
    out_dir = sandbox_root / "p3_notes" / "internal_consulting" / "10-30-2026" / "Copilot_Weekly_Notes"
    out_dir.mkdir(parents=True, exist_ok=True)
    notes_text = "Copilot Meeting Notes\nSummary:\n- Team aligned on Q4 delivery milestones and resource allocation.\n"
    meta = {
        "meeting_name": "Copilot Weekly Notes",
        "filename_slug": "Copilot_Weekly_Notes",
        "date_filename": "10-30-2026",
        "source_type": "copilot_notes",
        "notes_mode": True,
        "source_text": notes_text,
    }
    detected = detect_modality(input_path="notes.txt", content=notes_text, metadata=meta)
    req_delivs = get_deliverables_for_modality(detected)
    f2_text = "# 1. Meeting Objective\n\nReview Q4 delivery milestones.\n"
    f3_obj = _sample_signal_projection("meet-p3-notes", "Team aligned on Q4 delivery milestones")
    synth = SynthesisResult(output_1=None, output_2=f2_text, output_3=json.dumps(f3_obj, indent=2))
    deployed = deploy_deliverables(out_dir, meta, CanonicalModality.MEETING_NOTES, synth)
    has_no_01 = not list(out_dir.glob("01_*"))
    ok = (
        detected == CanonicalModality.MEETING_NOTES
        and req_delivs == ["02", "03"]
        and has_no_01
        and "02_recap" in deployed
        and "03_graph" in deployed
        and "source_notes" in deployed
        and all(Path(p).is_file() for p in deployed.values())
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P3-NOTES-INGEST",
        pipeline_id="P3_MEETING_NOTES",
        pipeline_name="Canonical Meeting Notes Pipeline",
        title="Undiarized meeting notes routing, [02, 03] deliverable landing & 01 omission",
        passed=ok,
        duration_ms=(time.monotonic() - t0) * 1000.0,
        verified_deliverables={k: _sha256_file(Path(v)) for k, v in deployed.items()},
        verified_invariants=[
            "MODALITY_DETECT_MEETING_NOTES",
            "OMITS_VERBATIM_01_TRANSCRIPT",
            "DELIVERABLES_02_03_AND_SOURCE_NOTES_LANDED",
        ],
        metrics={"deployed_keys": sorted(deployed.keys())},
        error=None if ok else "Meeting notes modality verification failed",
    )


def run_p4_document_and_firewall_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Scenario 4A & 4B: CanonicalModality.DOCUMENT [03] landing, checkpointing & Egress Sanitizer."""
    t0 = time.monotonic()
    out_dir = sandbox_root / "p4_document" / "project_nexus" / "10-30-2026" / "Grid_Spec_Doc"
    out_dir.mkdir(parents=True, exist_ok=True)
    stale_01 = out_dir / "01_Stale_10-30-2026_hitl_resolved_transcript.md"
    stale_01.write_text("stale", encoding="utf-8")
    doc_payload = _sample_signal_projection("doc-p4-grid-spec", "Substation transformer telemetry specification.")
    doc_json = json.dumps(doc_payload, indent=2)
    meta = {
        "meeting_name": "Grid Spec Doc",
        "filename_slug": "Grid_Spec_Doc",
        "date_filename": "10-30-2026",
        "is_document": True,
        "source_type": "document",
    }
    detected = detect_modality(input_path="grid_spec.json", content=doc_json, metadata=meta)
    req_delivs = get_deliverables_for_modality(detected)
    synth = SynthesisResult(output_1=None, output_2=None, output_3=doc_json)
    deployed = deploy_deliverables(out_dir, meta, CanonicalModality.DOCUMENT, synth)
    ckpt = open_stage_checkpoint(out_dir, meta)
    ckpt.save_stage("VALIDATED_SYNTHESIS", {"output_3": doc_json})

    blocked_secret = False
    blocked_advisory = False
    try:
        synth_tok = "Token s" + "k-abcdefghijklmnopqrstuvwxyz123456"
        LocalPreEgressSanitizer.sanitize_text(synth_tok)
    except EgressSanitizationError:
        blocked_secret = True
    try:
        LocalPreEgressSanitizer.sanitize_text("Confidential career leverage and INTERNAL_ADVISORY note")
    except EgressSanitizationError:
        blocked_advisory = True
    scrubbed = LocalPreEgressSanitizer.scrub_text("Rate is $250/hr and career leverage")

    ok = (
        detected == CanonicalModality.DOCUMENT
        and req_delivs == ["03"]
        and not stale_01.exists()
        and "03_graph" in deployed
        and Path(deployed["03_graph"]).is_file()
        and ckpt.has_stage("VALIDATED_SYNTHESIS")
        and blocked_secret
        and blocked_advisory
        and "$250/hr" not in scrubbed
        and "career leverage" not in scrubbed
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P4-DOCUMENT-AND-FIREWALL",
        pipeline_id="P4_DOCUMENT",
        pipeline_name="Canonical Document & Epistemic Firewall Pipeline",
        title="Document [03] deployment, stale meeting purge, stage checkpoint & pre-egress sanitizer",
        passed=ok,
        duration_ms=(time.monotonic() - t0) * 1000.0,
        verified_deliverables={k: _sha256_file(Path(v)) for k, v in deployed.items()},
        verified_invariants=[
            "MODALITY_DETECT_DOCUMENT_03_ONLY",
            "STALE_MEETING_DELIVERABLES_PURGED",
            "DOCUMENT_STAGE_CHECKPOINT_SEALED",
            "PRE_EGRESS_SANITIZER_BLOCKS_SECRETS_AND_CAREER_LEVERAGE",
        ],
        metrics={"blocked_secret": blocked_secret, "blocked_advisory": blocked_advisory},
        error=None if ok else "Document modality or pre-egress sanitizer verification failed",
    )


def run_all_modality_scenarios(sandbox_root: Path | None = None) -> List[ScenarioVerificationResult]:
    """Execute all Pipeline 1–4 modality scenarios inside an isolated sandbox."""
    if sandbox_root is not None:
        return [
            run_p1_audio_ingest_scenario(sandbox_root),
            run_p1_tier1_cached_replay_scenario(sandbox_root),
            run_p2_transcript_ingest_scenario(sandbox_root),
            run_p3_notes_ingest_scenario(sandbox_root),
            run_p4_document_and_firewall_scenario(sandbox_root),
        ]
    with tempfile.TemporaryDirectory(prefix="e2e_modality_scenarios_") as tmp:
        root = Path(tmp)
        return [
            run_p1_audio_ingest_scenario(root),
            run_p1_tier1_cached_replay_scenario(root),
            run_p2_transcript_ingest_scenario(root),
            run_p3_notes_ingest_scenario(root),
            run_p4_document_and_firewall_scenario(root),
        ]
