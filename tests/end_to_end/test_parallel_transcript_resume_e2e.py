"""
tests/end_to_end/test_parallel_transcript_resume_e2e.py

End-to-End verification for parallel raw transcript resume recovery:
1. Run #2 (09/23/2026 DocIQ Q4 Scope): Unsealed Attempt 3 crash directory rejection,
   deadline suspension across multi-hour HITL wait, and signed operator override
   restoration (PASS_AUTHORIZED_EXCEPTION -> ReleaseVerdict.PASS).
2. Run #3 (09/30/2026 DOCIQ_Q4_Scope_Bailey_Amit): Stakeholder roster parenthetical
   ("Bailey Morton (PartnerAdvisory), Amit Ayer (PartnerAdvisory)") org-vs-role healing on --resume
   and --entity-resolutions propagation through Gate 3.
3. Guard against reusing genuinely failed checkpoints when no HITL override or
   deterministic heal applies.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

from config.request_deadline import check_deadline, deadline_scope
from dual_pipeline.canonical_pipeline.c0_pa_runner import step_c0_pa_with_resume
from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _read_attempt_checkpoint,
    memoize_substage_checkpoints,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    FullMeetingSynthesisStrategy,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.llm_judge_core.judge_clarification import (
    evaluate_judge_clarification,
)
from dual_pipeline.release_policy import ReleaseVerdict
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)
from dual_pipeline.runtime.pipeline_context import (
    compute_stage_manifest,
    save_stage_checkpoint,
)
from dual_pipeline.transcript_qa_core.evaluator import _eval_output_n
from dual_pipeline.transcript_qa_core.graph_reference_validator import (
    check_conflicting_duplicates,
)


def _build_valid_transcript_01(date_str: str = "09/23/2026") -> str:
    turns = [
        f"**[00:00:{10 + i:02d}] Bailey Morton:** We are aligning on DocIQ Q4 scope and PartnerAdvisory delivery milestones for item {i}."
        if i % 2 == 0
        else f"**[00:00:{10 + i:02d}] Amit Ayer:** Agreed, let us track the DocIQ architecture and PartnerAdvisory workstream item {i}."
        for i in range(12)
    ]
    return (
        f"# DocIQ Q4 Scope\n"
        f"**Date:** {date_str}\n"
        f"**Attendees:** Bailey Morton (PartnerAdvisory), Amit Ayer (PartnerAdvisory)\n\n"
        + "\n\n".join(turns)
        + "\n"
    )


def _build_valid_recap_02(date_str: str = "09/23/2026") -> str:
    return (
        f"# Executive Recap — DocIQ Q4 Scope\n"
        f"**Date:** {date_str}\n\n"
        f"## Executive Summary\n"
        f"Bailey Morton and Amit Ayer aligned on DocIQ Q4 scope and PartnerAdvisory delivery milestones. "
        + ("Detailed recap narrative sentence for verification. " * 20)
        + "\n"
    )


def _build_graph_03_json(include_conflicting_role_partner_advisory: bool = False) -> str:
    entity_upserts = [
        {
            "id": "org:partner_advisory",
            "type": "org",
            "name": "PartnerAdvisory",
            "description": "Consulting partner organization.",
            "mutation": "CONFIRM",
            "evidence_ids": ["ev-01"],
        },
        {
            "id": "person:bailey-morton",
            "type": "person",
            "name": "Bailey Morton",
            "description": "DocIQ Product Owner at PartnerAdvisory.",
            "mutation": "CONFIRM",
            "evidence_ids": ["ev-01"],
        },
        {
            "id": "person:amit-ayer",
            "type": "person",
            "name": "Amit Ayer",
            "description": "AI Architect at PartnerAdvisory.",
            "mutation": "CONFIRM",
            "evidence_ids": ["ev-01"],
        },
    ]
    relationship_signals = [
        {
            "signal_id": "rel-bailey--partner_advisory--MEMBER_OF",
            "from_id": "person:bailey-morton",
            "to_id": "org:partner_advisory",
            "relationship": "MEMBER_OF",
            "predicate": "MEMBER_OF",
            "evidence_ids": ["ev-01"],
        },
        {
            "signal_id": "rel-amit--partner_advisory--MEMBER_OF",
            "from_id": "person:amit-ayer",
            "to_id": "org:partner_advisory",
            "relationship": "MEMBER_OF",
            "predicate": "MEMBER_OF",
            "evidence_ids": ["ev-01"],
        },
    ]
    if include_conflicting_role_partner_advisory:
        entity_upserts.append(
            {
                "id": "role:partner_advisory",
                "type": "role",
                "name": "PartnerAdvisory",
                "description": "Professional role: PartnerAdvisory.",
                "mutation": "ADD",
                "evidence_ids": ["ev-01"],
            }
        )
        relationship_signals.append(
            {
                "signal_id": "fact-rel-3--person-bailey-morton--role-partner_advisory--HAS_ROLE",
                "from_id": "person:bailey-morton",
                "to_id": "role:partner_advisory",
                "relationship": "HAS_ROLE",
                "predicate": "HAS_ROLE",
                "evidence_ids": ["ev-01"],
            }
        )
    payload = {
        "schema_version": "5.7",
        "meeting_id": "mtg-09-30-2026-bailey-amit",
        "meeting_date": "09/30/2026",
        "project": "dociq",
        "evidence_index": {
            "ev-01": {
                "statement_id": "ev-01",
                "speaker": "Bailey Morton",
                "exact_text": "We are aligning on DocIQ Q4 scope and PartnerAdvisory delivery milestones for item 0.",
                "locator": {"type": "timestamp", "value": "00:00:10"},
            }
        },
        "graph_mutations": {
            "entity_upserts": entity_upserts,
            "relationship_signals": relationship_signals,
            "decision_updates": [],
            "risk_updates": [],
            "proposal_updates": [],
            "action_updates": [],
        },
    }
    return json.dumps(payload, indent=2)


def test_run2_dociq_q4_scope_resume_rejects_unsealed_attempt3_and_honors_hitl_override(
    tmp_path: Path,
) -> None:
    """E2E test for Run #2 (09/23/2026 DocIQ Q4 Scope):
    - Rejects unsealed attempt_3 crash directory during memoization.
    - Suspends deadline during 2-hour interactive HITL wait.
    - Reuses operator-affirmed Output 02 from attempt_2 without re-invoking LLM.
    - Restores signed HITL clarification receipt and authorizes release (PASS).
    """
    out_p = tmp_path / "sandbox_run2_2026-09-23_dociq_q4_scope"
    att2_dir = out_p / "attempts" / "attempt_2"
    att3_dir = out_p / "attempts" / "attempt_3"
    att2_dir.mkdir(parents=True, exist_ok=True)
    att3_dir.mkdir(parents=True, exist_ok=True)

    ledger_path = tmp_path / "hitl_decision_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_path, use_sqlite=False)

    meta: Dict[str, Any] = {
        "project": "dociq",
        "meeting_name": "DocIQ Q4 Scope",
        "date": "09/23/2026",
        "attendees": "Bailey Morton (PartnerAdvisory), Amit Ayer (PartnerAdvisory)",
        "reviewer": "amit_operator",
        "hitl_ledger_path": str(ledger_path),
        "resume": True,
        "output_dir": str(out_p),
    }

    t01 = _build_valid_transcript_01("09/23/2026")
    r02 = _build_valid_recap_02("09/23/2026")
    g03 = _build_graph_03_json(include_conflicting_role_partner_advisory=False)

    mf1 = compute_stage_manifest("output_1", t01, meta)
    mf2 = compute_stage_manifest("output_2", t01, meta)
    mf3 = compute_stage_manifest("output_3", t01, meta)

    save_stage_checkpoint(att2_dir / "output_1.ckpt.md", t01, mf1)
    save_stage_checkpoint(att2_dir / "output_2.ckpt.md", r02, mf2)
    save_stage_checkpoint(att2_dir / "output_3.ckpt.json", g03, mf3)

    att2_decision = {
        "attempt": 2,
        "status": "FAIL",
        "sealed": True,
        "authoritative_write_authorized": False,
        "output_1_deterministic_gates_pass": True,
        "output_1_llm_score_pass": True,
        "output_1_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_2_llm_score_pass": False,
        "output_2_pass": False,
        "output_3_deterministic_gates_pass": True,
        "output_3_llm_score_pass": True,
        "output_3_pass": True,
    }
    (att2_dir / "final_release_decision.json").write_text(
        json.dumps(att2_decision), encoding="utf-8"
    )

    # Attempt 3 crashed with RequestDeadlineExceeded before writing final_release_decision.json
    (att3_dir / "output_1.ckpt.md").write_text("UNSEALED_CRASH_01 " * 40, encoding="utf-8")
    (att3_dir / "output_2.ckpt.md").write_text("UNSEALED_CRASH_02 " * 40, encoding="utf-8")
    (att3_dir / "output_3.ckpt.json").write_text(g03, encoding="utf-8")

    # 1. Unsealed attempt_3 must be rejected by _read_attempt_checkpoint
    assert _read_attempt_checkpoint(att3_dir, "output_2.ckpt.md", "output_2_pass") is None

    # 2. memoize_substage_checkpoints must freeze Output 01 and 03 from attempt_2, NOT attempt_3
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    memo = memoize_substage_checkpoints(out_p, sm, meta, CanonicalModality.MEETING_TRANSCRIPT)
    assert memo["output_1"] is True
    assert memo["output_2"] is False
    assert memo["output_3"] is True
    assert sm.get_frozen_content("output_1") == t01

    # 3. Record signed HITL clarification during simulated 2-hour interactive pause
    fake_clock = [1000.0]

    def fake_monotonic() -> float:
        return fake_clock[0]

    def slow_interactive_resolver(_envelope: object) -> str:
        fake_clock[0] += 7380.0  # 2h 03m interactive wait (02:47 -> 04:50)
        return "AFFIRM_VALID"

    with patch("config.request_deadline.time.monotonic", side_effect=fake_monotonic):
        with deadline_scope(900.0) as dl:
            fake_clock[0] += 120.0
            initial_report: Dict[str, Any] = {
                "status": "FAIL",
                "overall_verdict": "FAIL",
                "output_1_evaluation": {"status": "PASS", "verdict": "PASS", "composite_score": 93},
                "output_2_evaluation": {
                    "status": "HITL_REQUIRED",
                    "verdict": "HITL_REQUIRED",
                    "composite_score": 87.0,
                    "section_scores": {
                        "header_and_overview": 9,
                        "key_updates_technical_substance": 31,
                        "conditional_extensions": 17,
                        "appendix_stakeholder_perspectives": 30,
                    },
                    "forensic_rationale": "Minor conciseness deduction on Q4 scope.",
                },
                "output_3_evaluation": {"status": "PASS", "verdict": "PASS", "composite_score": 92},
            }
            initial_decision: Dict[str, Any] = {
                "attempt": 3,
                "max_attempts": 3,
                "status": "FAIL",
                "output_1_deterministic_gates_pass": True,
                "output_1_pass": True,
                "output_2_deterministic_gates_pass": True,
                "output_2_pass": False,
                "output_3_deterministic_gates_pass": True,
                "output_3_pass": True,
            }
            clarified = evaluate_judge_clarification(
                initial_report,
                initial_decision,
                meta,
                store=store,
                interactive_resolver=slow_interactive_resolver,
            )
            assert clarified is True
            assert dl is not None
            assert abs(dl.remaining() - 780.0) < 1e-3
            check_deadline()

            # 4. Now verify --resume synthesis reuses attempt_2's operator-affirmed Output 02
            mock_forensic = MagicMock()
            mock_recap = MagicMock()
            mock_graph = MagicMock()
            strategy = FullMeetingSynthesisStrategy(
                provider=MagicMock(),
                l2_forensic=mock_forensic,
                l2_recap=mock_recap,
                l2_graph=mock_graph,
            )
            pa_context = {"output_dir": str(out_p), "state_machine": sm, "metadata": meta}
            synth_res = strategy.synthesize(
                source_text=t01,
                pa_context=pa_context,
                metadata=meta,
                attempt=3,
            )
            mock_forensic.execute.assert_not_called()
            mock_recap.execute.assert_not_called()
            mock_graph.execute.assert_not_called()
            assert synth_res.output_2 == r02

            # 5. Verify _eval_output_n honors PASS_AUTHORIZED_EXCEPTION
            verdict, pass_flag = _eval_output_n(
                2, initial_report["output_2_evaluation"], 87, 90
            )
            assert verdict == ReleaseVerdict.PASS
            assert pass_flag is True


def test_run3_dociq_bailey_amit_resume_heals_partner_advisory_org_role_conflict(
    tmp_path: Path,
) -> None:
    """E2E test for Run #3 (09/30/2026 DOCIQ_Q4_Scope_Bailey_Amit):
    - Prior attempt_1 failed deterministic Gate 3 due to role:partner_advisory vs org:partner_advisory.
    - On --resume with --entity-resolutions, step_c0_pa_with_resume syncs resolutions,
      and _resolve_output_3 deterministically heals attempt_1/output_3.ckpt.json
      without calling l2_graph.execute, clearing CONFLICTING_DUPLICATE_ENTITY.
    """
    out_p = tmp_path / "sandbox_run3_2026-09-30_dociq_bailey_amit"
    att1_dir = out_p / "attempts" / "attempt_1"
    att1_dir.mkdir(parents=True, exist_ok=True)

    t01 = _build_valid_transcript_01("09/30/2026")
    r02 = _build_valid_recap_02("09/30/2026")
    conflicted_g03 = _build_graph_03_json(include_conflicting_role_partner_advisory=True)

    # Confirm raw unhealed graph fails check_conflicting_duplicates
    dup_ok, dup_errs = check_conflicting_duplicates(json.loads(conflicted_g03))
    assert dup_ok is False
    assert any("CONFLICTING_DUPLICATE_ENTITY" in err for err in dup_errs)

    checkpoint = MagicMock()
    checkpoint.has_stage.return_value = True
    checkpoint.get_stage.return_value = {
        "pa_context": {
            "metadata": {
                "project": "dociq",
                "meeting_name": "DOCIQ_Q4_Scope_Bailey_Amit",
                "date": "09/30/2026",
                "attendees": "Bailey Morton (PartnerAdvisory), Amit Ayer (PartnerAdvisory)",
            }
        },
        "resolved_transcript": t01,
        "speaker_map": {},
    }
    meta: Dict[str, Any] = {
        "project": "dociq",
        "meeting_name": "DOCIQ_Q4_Scope_Bailey_Amit",
        "date": "09/30/2026",
        "attendees": "Bailey Morton (PartnerAdvisory), Amit Ayer (PartnerAdvisory)",
        "entity_resolutions": {"PartnerAdvisory": "org:partner_advisory"},
        "resume": True,
        "output_dir": str(out_p),
    }

    pa_context, _ = step_c0_pa_with_resume(
        pipeline=MagicMock(),
        checkpoint=checkpoint,
        should_resume=True,
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        source_text=t01,
        input_hash="run3_hash",
        meta=meta,
        presentation_path=None,
        out_p=out_p,
        tracker=MagicMock(),
    )
    assert pa_context["metadata"]["entity_resolutions"] == {"PartnerAdvisory": "org:partner_advisory"}

    mf1 = compute_stage_manifest("output_1", t01, pa_context["metadata"])
    mf2 = compute_stage_manifest("output_2", t01, pa_context["metadata"])
    mf3 = compute_stage_manifest("output_3", t01, pa_context["metadata"])

    save_stage_checkpoint(att1_dir / "output_1.ckpt.md", t01, mf1)
    save_stage_checkpoint(att1_dir / "output_2.ckpt.md", r02, mf2)
    save_stage_checkpoint(att1_dir / "output_3.ckpt.json", conflicted_g03, mf3)

    att1_decision = {
        "attempt": 1,
        "status": "FAIL",
        "sealed": True,
        "authoritative_write_authorized": False,
        "output_1_deterministic_gates_pass": True,
        "output_1_llm_score_pass": True,
        "output_1_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_2_llm_score_pass": False,
        "output_2_pass": False,
        "output_3_deterministic_gates_pass": False,
        "output_3_llm_score_pass": False,
        "output_3_pass": False,
    }
    (att1_dir / "final_release_decision.json").write_text(
        json.dumps(att1_decision), encoding="utf-8"
    )

    sm = SelectiveRemediationStateMachine(max_attempts=3)
    pa_context["state_machine"] = sm
    pa_context["output_dir"] = str(out_p)

    mock_graph = MagicMock()
    strategy = FullMeetingSynthesisStrategy(
        provider=MagicMock(),
        l2_forensic=MagicMock(),
        l2_recap=MagicMock(),
        l2_graph=mock_graph,
    )
    healed_out_3 = strategy._resolve_output_3(
        forensic_transcript=t01,
        executive_recap=None,
        pa_context=pa_context,
        attempt=2,
        remediation_directive=None,
        progress_notify=None,
        ckpt_dir=out_p / "attempts" / "attempt_2",
    )
    # DeterministicHealer healed role:partner_advisory -> org:partner_advisory so Gate 3 conflict check passes without LLM call
    mock_graph.execute.assert_not_called()
    healed_parsed = json.loads(healed_out_3)
    dup_ok_after, dup_errs_after = check_conflicting_duplicates(healed_parsed)
    assert dup_ok_after is True, f"Expected zero conflicting entities, got: {dup_errs_after}"
    healed_ids = {e["id"] for e in healed_parsed["graph_mutations"]["entity_upserts"]}
    assert "org:partner_advisory" in healed_ids
    assert "role:partner_advisory" not in healed_ids


def test_failed_judge_checkpoint_without_override_triggers_regeneration(
    tmp_path: Path,
) -> None:
    """Guard test: when a prior attempt checkpoint failed LLM Judge and has no HITL
    override, _resolve_output_2 must NOT reuse the failed checkpoint as passed."""
    out_p = tmp_path / "sandbox_guard_failed_ckpt"
    att1_dir = out_p / "attempts" / "attempt_1"
    att1_dir.mkdir(parents=True, exist_ok=True)

    t01 = _build_valid_transcript_01("09/23/2026")
    failed_r02 = _build_valid_recap_02("09/23/2026")
    regenerated_r02 = failed_r02 + "\n## Remediation Addendum\nUpdated Q4 scope details.\n"

    meta: Dict[str, Any] = {
        "project": "dociq",
        "meeting_name": "Guard_Failed_Recap_Test",
        "output_dir": str(out_p),
    }
    mf2 = compute_stage_manifest("output_2", t01, meta)
    save_stage_checkpoint(att1_dir / "output_2.ckpt.md", failed_r02, mf2)
    (att1_dir / "final_release_decision.json").write_text(
        json.dumps(
            {
                "attempt": 1,
                "status": "FAIL",
                "output_2_deterministic_gates_pass": True,
                "output_2_llm_score_pass": False,
                "output_2_pass": False,
            }
        ),
        encoding="utf-8",
    )

    mock_recap = MagicMock()
    mock_recap.execute.return_value = regenerated_r02
    strategy = FullMeetingSynthesisStrategy(
        provider=MagicMock(),
        l2_forensic=MagicMock(),
        l2_recap=mock_recap,
        l2_graph=MagicMock(),
    )
    out_2 = strategy._resolve_output_2(
        forensic_transcript=t01,
        pa_context={"output_dir": str(out_p), "metadata": meta},
        attempt=2,
        remediation_directive=None,
        progress_notify=None,
        ckpt_dir=out_p / "attempts" / "attempt_2",
    )
    mock_recap.execute.assert_called_once()
    assert "Remediation Addendum" in out_2
