"""
E2E verification for EVALS_RELEASE promotion and resumption when final_release_decision
status is PASS_AUTHORIZED_EXCEPTION (Task-237 regression).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dual_pipeline.canonical_pipeline.attachments import (
    _load_passing_decision,
    try_resume_passing_attempt,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.pipeline import _resolve_evals_mutation_intent
from dual_pipeline.canonical_pipeline.publish_helper import (
    _assert_authoritative_mutation_preconditions,
    is_eval_verdict_pass,
)
from dual_pipeline.transcript_pipeline_core.release_runner import (
    promote_staging_to_release,
)


def test_evals_release_authorized_exception_promotion_and_resume_e2e(tmp_path: Path) -> None:
    """
    End-to-end lifecycle verification:
    1. A synthesis run resolves to PASS_AUTHORIZED_EXCEPTION (Output 1=90 PASS, Output 2=93 PASS,
       Output 3=87 PASS_AUTHORIZED_EXCEPTION).
    2. _resolve_evals_mutation_intent and _assert_authoritative_mutation_preconditions authorize
       mutation and release promotion without throwing RuntimeError.
    3. promote_staging_to_release promotes staged deliverables (including 02_*_executive_recap.md)
       to the release directory.
    4. Subsequent try_resume_passing_attempt recognizes the PASS_AUTHORIZED_EXCEPTION attempt_2
       checkpoint without re-running L2 synthesis.
    """
    out_p = tmp_path / "apex_global_Energy_PartnerAdvisory"
    out_p.mkdir(parents=True, exist_ok=True)
    staging_dir = tmp_path / ".staging_apex_global_Energy_PartnerAdvisory_52a88487"
    staging_dir.mkdir(parents=True, exist_ok=True)

    final_decision = {
        "attempt": 2,
        "status": "PASS_AUTHORIZED_EXCEPTION",
        "release_decision": "PASS_AUTHORIZED_EXCEPTION",
        "final_pass": True,
        "authoritative_write_authorized": True,
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": True,
        "failed_artifacts": [],
        "artifact_manifest": {
            "judge_evaluation_digest": "sha256:abc1234567890",
        },
    }
    judge_report = {
        "status": "PASS_AUTHORIZED_EXCEPTION",
        "overall_verdict": "PASS_AUTHORIZED_EXCEPTION",
        "verdict": "PASS_AUTHORIZED_EXCEPTION",
        "evaluation_receipt_digest": "sha256:abc1234567890",
        "output_1_evaluation": {"composite_score": 90.0, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 93.0, "verdict": "PASS"},
        "output_3_evaluation": {
            "composite_score": 87.0,
            "verdict": "PASS_AUTHORIZED_EXCEPTION",
            "authorized_exception": True,
        },
    }
    qa_report = {"status": "PASS", "passed": True}

    eval_result = SimpleNamespace(
        passed=True,
        authoritative_write_authorized=True,
        final_decision=final_decision,
        judge_report=judge_report,
        qa_report=qa_report,
    )

    assert is_eval_verdict_pass(eval_result) is True
    is_pass, should_mutate = _resolve_evals_mutation_intent(
        meta={"reviewer": "operator"},
        mutate_graph=True,
        auto_mutate_on_pass=True,
        eval_result=eval_result,
        reviewer="operator",
    )
    assert is_pass is True
    assert should_mutate is True

    # Write staged deliverables and decision files
    (staging_dir / "01_apex_global_Energy_PartnerAdvisory_10-02-2026_00000129_hitl_resolved_transcript.md").write_text(
        "# Transcript\n**00:00:01 Speaker:** Hello.", encoding="utf-8"
    )
    (staging_dir / "02_apex_global_Energy_PartnerAdvisory_10-02-2026_00000129_executive_recap.md").write_text(
        "# Executive Recap\nValidated 93.0 score recap.", encoding="utf-8"
    )
    (staging_dir / "03_apex_global_Energy_PartnerAdvisory_10-02-2026_00000129_canonical_signal_projection.json").write_text(
        json.dumps({"graph_mutations": {"entity_upserts": []}}), encoding="utf-8"
    )
    (staging_dir / "final_release_decision.json").write_text(
        json.dumps(final_decision, indent=2), encoding="utf-8"
    )
    (staging_dir / "llm_judge_evaluation.json").write_text(
        json.dumps(judge_report, indent=2), encoding="utf-8"
    )
    (staging_dir / "qa_gates_report.json").write_text(
        json.dumps(qa_report, indent=2), encoding="utf-8"
    )

    # Preconditions check must not raise RuntimeError on PASS_AUTHORIZED_EXCEPTION
    _assert_authoritative_mutation_preconditions(staging_dir, metadata={})

    # Promote staging to release
    promote_staging_to_release(staging_dir, out_p, purge_staging=False)
    promoted_02 = out_p / "02_apex_global_Energy_PartnerAdvisory_10-02-2026_00000129_executive_recap.md"
    assert promoted_02.is_file()
    assert "Validated 93.0 score recap." in promoted_02.read_text(encoding="utf-8")

    # Verify attempt_2 checkpoint resumption with PASS_AUTHORIZED_EXCEPTION
    att2 = out_p / "attempts" / "attempt_2"
    att2.mkdir(parents=True, exist_ok=True)
    (att2 / "output_1.ckpt.md").write_text("# Transcript\n", encoding="utf-8")
    (att2 / "output_2.ckpt.md").write_text("# Executive Recap\n", encoding="utf-8")
    (att2 / "output_3.ckpt.json").write_text("{}", encoding="utf-8")
    (att2 / "final_release_decision.json").write_text(json.dumps(final_decision), encoding="utf-8")
    (att2 / "llm_judge_evaluation.json").write_text(json.dumps(judge_report), encoding="utf-8")
    (att2 / "qa_gates_report.json").write_text(json.dumps(qa_report), encoding="utf-8")

    assert _load_passing_decision(att2) is not None
    resumed = try_resume_passing_attempt(out_p, {"resume": True}, CanonicalModality.MEETING_TRANSCRIPT)
    assert resumed is not None
    att_num, syn_res, ev_res = resumed
    assert att_num == 2
    assert ev_res.final_decision["status"] == "PASS_AUTHORIZED_EXCEPTION"


def test_wave2_role_reconciler_and_graph_healer_endpoint_e2e() -> None:
    """
    Wave 2 E2E verification:
    - Conference room endpoints (MOLE-VC) are skipped by speaker_role_reconciler and deduplicated
      cleanly as system:mole-vc by graph_healer without creating duplicate IDs.
    - Compound titles containing '&' or 'and' remain atomic role entities.
    - Placeholder org strings ('Organization unverified', 'Internal') never spawn org: nodes.
    - hitl_role_resolutions and hitl_org_resolutions thread into _item_lineage and ALLOWED_MEETING_METADATA_KEYS.
    """
    from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
    from dual_pipeline.transcript_stages_core.graph_executor import _item_lineage
    from dual_pipeline.transcript_stages_core.recap_executor import ALLOWED_MEETING_METADATA_KEYS
    from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
        reconcile_speaker_role_resolutions,
    )

    assert "hitl_role_resolutions" in ALLOWED_MEETING_METADATA_KEYS
    assert "hitl_org_resolutions" in ALLOWED_MEETING_METADATA_KEYS

    metadata = {
        "roster": [
            {"name": "Nathan Sutton", "role": "Senior Director, Enterprise Compute & Asset Tech Solutions", "org": "Apex Global Enterprises"},
            {"name": "MOLE-VC", "role": "Conference Room Endpoint", "org": "Apex Global Enterprises"},
        ],
        "hitl_role_resolutions": {
            "Christian Moberg": "Principal DevOps Engineer",
            "Nathan Sutton": "Senior Director, Enterprise Compute & Asset Tech Solutions",
        },
        "hitl_org_resolutions": {
            "Christian Moberg": "Organization unverified",
            "Nathan Sutton": "Apex Global Enterprises",
        },
    }

    lineage_cm = _item_lineage({"speaker": "Christian Moberg"}, metadata, "mtg-10-02")
    assert lineage_cm["speaker_role"] == "Principal DevOps Engineer"
    assert lineage_cm["speaker_org"] == "Organization unverified"

    raw_projection = {
        "meeting_id": "mtg-10-02",
        "evidence_index": {
            "ev-001": {
                "statement_id": "ev-001",
                "speaker": "Nathan Sutton",
                "exact_text": "We govern Enterprise Compute & Asset Tech Solutions from MOLE-VC.",
                "grounding_status": "GROUNDED",
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:nathan-sutton", "type": "person", "name": "Nathan Sutton", "evidence_ids": ["ev-001"]},
                {"id": "person:mole-vc", "type": "person", "name": "MOLE-VC", "evidence_ids": ["ev-001"]},
                {"id": "system:mole-vc", "type": "system", "name": "MOLE-VC", "evidence_ids": ["ev-001"]},
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig_mole_vc",
                    "from_id": "person:nathan-sutton",
                    "to_id": "system:mole-vc",
                    "relationship": "USES",
                    "evidence_ids": ["ev-001"],
                }
            ],
        },
    }

    reconciled = reconcile_speaker_role_resolutions(raw_projection, metadata=metadata)
    healed_str, _changes = DeterministicHealer.heal_output_3(reconciled, metadata=metadata)
    healed = json.loads(healed_str)
    upserts = healed["graph_mutations"]["entity_upserts"]
    ids = [e.get("id") for e in upserts]

    # Zero duplicate system:mole-vc and zero person:mole-vc
    assert ids.count("system:mole-vc") == 1
    assert "person:mole-vc" not in ids
    assert "person:mole_vc" not in ids

    # Zero org:organization_unverified
    assert not any("unverified" in str(eid).lower() for eid in ids)

    # Compound role with '&' preserved intact (not split into two broken role nodes)
    role_names = [e.get("name") for e in upserts if e.get("type") == "role"]
    assert any("Enterprise Compute & Asset Tech Solutions" in str(rn) for rn in role_names)

