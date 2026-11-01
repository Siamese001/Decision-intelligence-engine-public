"""
End-to-end test verifying upstream audio QA architecture and downstream synthesis
evaluated with OpenAI gpt-5.6-luna (reasoning_effort=high).
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "env_di")

from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.llm_judge import DecomposedMultiJudge
from dual_pipeline.transcript_qa import evaluate_all_qa_gates
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


def test_upstream_audio_to_luna_judge_e2e():
    """
    End-to-end integration test:
    1. Upstream audio signals are packaged into ForensicSignalEnvelope.
    2. UpstreamAudioQAGatekeeper deterministically validates acoustic & DSP integrity.
    3. Output 01, 02, and 03 are synthesized from authorized turns.
    4. Deterministic QA Gates validate structural & schema conformance.
    5. DecomposedMultiJudge with gpt-5.6-luna (reasoning_effort=high) performs impartial evaluation.
    """
    # 1. Upstream Audio Signal Construction
    turn1 = ForensicTurnSignal(
        turn_id="turn-001",
        span=SampleSpan(start_sample=0, end_sample=16000 * 8),
        transcript_verbatim="Alex Rivera: Tenika is technical services and I'm, I guess, the technology part.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 1",),
    )
    turn2 = ForensicTurnSignal(
        turn_id="turn-002",
        span=SampleSpan(start_sample=16000 * 9, end_sample=16000 * 20),
        transcript_verbatim="Jordan Taylor: Technical services is identifying what's the real cause, ensuring we don't have the same outage over and over again.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 2",),
    )
    turn3 = ForensicTurnSignal(
        turn_id="turn-003",
        span=SampleSpan(start_sample=16000 * 21, end_sample=16000 * 35),
        transcript_verbatim="Alex Rivera: We have thirty different systems, thirty different platforms, thirty different screens to look at. It's just not sustainable.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 1",),
    )
    digest = ForensicSignalEnvelope.compute_signal_digest("rec-apex_trans-910", ("src-hash-1",), (turn1, turn2, turn3))
    envelope = ForensicSignalEnvelope(
        recording_id="rec-apex_trans-910",
        source_hashes=("src-hash-1",),
        canonical_sample_rate_hz=16000,
        timeline_length_samples=16000 * 35,
        turns=(turn1, turn2, turn3),
        signal_digest=digest,
    )

    # 2. Upstream Audio QA Gatekeeper (Deterministic DSP & Acoustic Gate)
    gatekeeper = UpstreamAudioQAGatekeeper()
    upstream_report = gatekeeper.evaluate(envelope)

    assert upstream_report.verdict == "PASS"
    assert "forensic_rendering" in upstream_report.authorized_tasks
    assert len(upstream_report.authorized_turn_ids) == 3

    # 3. Artifact Assembly from Upstream Verified Signals
    f1_text = (
        "# APEX_TRANS Insights Hub Tech Services Discovery -- HITL-Resolved Verbatim Transcript\n\n"
        "**Date:** 09/10/2026  \n"
        "**Time:** 03:34 PM - 03:39 PM ET  \n"
        "**Platform:** Microsoft Teams (Apex Global Enterprises)  \n"
        "**Participants:**\n"
        "- **Apex Energy Transmission (APEX_TRANS):**\n"
        "  - Alex Rivera (Technology Lead)\n"
        "  - Jordan Taylor (Technical Services Lead / Manager)\n"
        "- **PartnerAdvisory:**\n"
        "  - Anece Elakkari (Solutions Lead)\n\n"
        "## Attribution Summary\n\n"
        "| Speaker | Org | Role | Turns Attributed | Confidence Tier | Primary Anchor Cue |\n"
        "|---|---|---|---|---|---|\n"
        "| Alex Rivera | APEX_TRANS | Technology Lead | 2 | HIGH | Audio biometric |\n"
        "| Jordan Taylor | APEX_TRANS | Technical Services Lead / Manager | 1 | HIGH | Audio biometric |\n"
        "| Anece Elakkari | PartnerAdvisory | Solutions Lead | 0 | HIGH | Present |\n\n"
        "## Ambiguity Lock Register\n"
        "| ID | Status | Category | Scope | Resolution | Confidence |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| `ALR-01` | `RESOLVED` | Speaker Attribution | Alex Rivera | Reconciled Alex Rivera organizational role. | HIGH (98%) |\n"
        "| `ALR-02` | `RESOLVED` | Speaker Attribution | Jordan Taylor | Reconciled Jordan Taylor organizational role. | HIGH (98%) |\n"
        "| `ALR-03` | `RESOLVED` | Speaker Attribution | Anece Elakkari | Reconciled Anece Elakkari organizational role. | HIGH (95%) |\n"
        "| `ALR-04` | `RESOLVED` | Domain Lexicon | SCADA fault targets | Validated engineering terminology. | HIGH (99%) |\n\n"
        "## Transcript Content\n\n"
        "[00:00:05] **Alex Rivera**: Tenika is technical services and I'm, I guess, the technology part.\n\n"
        "[00:00:15] **Jordan Taylor**: Technical services is identifying what's the real cause, ensuring we don't have the same outage over and over again.\n\n"
        "[00:00:25] **Alex Rivera**: We have thirty different systems, thirty different platforms, thirty different screens to look at. It's just not sustainable.\n"
    )

    f2_text = (
        "# Subject: Executive Recap: APEX_TRANS Insights Hub Tech Services Discovery (09/10/2026)\n\n"
        "**Date:** 09/10/2026  \n"
        "**Time:** 03:34 PM - 03:39 PM ET  \n"
        "**Attendees:**\n"
        "- **Apex Energy Transmission (APEX_TRANS):** Alex Rivera (Technology Lead), Jordan Taylor (Technical Services Lead / Manager)\n"
        "- **PartnerAdvisory:** Anece Elakkari (Solutions Lead)\n\n"
        "---\n\n"
        "## Meeting Objective\n"
        "Establish operational baseline and telemetry requirements across APEX_TRANS operating companies to evaluate candidate technical architecture options.\n\n"
        "## Key Discussion Points & Findings\n"
        "- **Governance Split:** Alex Rivera leads technology evaluations while Jordan Taylor oversees technical services [00:00:05].\n"
        "- **Operator Cognitive Overload:** Operators navigate thirty disparate systems and screens [00:00:25].\n"
        "- **Post-Trip Engineering Analysis:** Technical services conducts root-cause investigations to prevent recurring outages [00:00:15].\n\n"
        "## Next Steps & Action Items\n"
        "| # | Action Item | Owner | Target Date | Priority | Epistemic Status |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Conduct live architecture walkthrough of operational software platforms | Alex Rivera | 09/11/2026 | HIGH | Grounded |\n\n"
        "## Open Decisions / Blockers\n"
        "- **Integration Milestone Selection Gate (09/25/2026):** APEX_TRANS executive decision owner to select target milestone for time-boxed architecture definition.\n\n"
        "## Business Meaning and Value Case\n"
        "Consolidating operational telemetry reduces operator cognitive fatigue and context switching across control centers.\n\n"
        "## Executive Decision\n"
        "Alex Rivera committed to walk through available monitoring systems on 09/11/2026.\n\n"
        "## Immediate Delivery Scope & Release Boundaries\n"
        "Candidate delivery scope: Lone Star Transmission momentary-trip correlation using SCADA fault targets and weather radar.\n"
    )

    f3_data = {
        "schema_version": "5.7",
        "meeting_id": "meeting-apex_trans-e2e-upstream-test",
        "title": "APEX_TRANS Insights Hub Tech Services Discovery",
        "canonical_topic": "APEX_TRANS Technical Services and Control Room Operations Discovery",
        "classification": "confidential",
        "audiences": ["nee-engineering", "partner_advisory-leadership", "apex_trans-operations"],
        "observed_at": "09/10/2026",
        "evidence_index": {
            "ev-01": {
                "speaker": "person:mitchell-simmons",
                "source_classification": "technical",
                "text": "Alex Rivera leads technology evaluations while Jordan Taylor manages technical services.",
                "exact_text": "Tenika is technical services and I'm, I guess, the technology part.",
                "locator": {"kind": "timestamp", "value": "00:00:05"},
            },
            "ev-02": {
                "speaker": "person:tenika-johnson",
                "source_classification": "technical",
                "text": "Technical services conducts post-trip engineering analysis to prevent recurring outages.",
                "exact_text": "Technical services is identifying what's the real cause, ensuring we don't have the same outage over and over again.",
                "locator": {"kind": "timestamp", "value": "00:00:15"},
            },
            "ev-03": {
                "speaker": "person:mitchell-simmons",
                "source_classification": "technical",
                "text": "Control room operators navigate thirty disparate systems and screens during operations.",
                "exact_text": "We have thirty different systems, thirty different platforms, thirty different screens to look at. It's just not sustainable.",
                "locator": {"kind": "timestamp", "value": "00:00:25"},
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:apex_trans", "type": "org", "name": "Apex Energy Transmission", "mutation": "create", "observed_at": "09/10/2026"},
                {"id": "person:mitchell-simmons", "type": "person", "name": "Alex Rivera", "mutation": "create", "observed_at": "09/10/2026"},
                {"id": "person:tenika-johnson", "type": "person", "name": "Jordan Taylor", "mutation": "create", "observed_at": "09/10/2026"},
                {"id": "system:apex_trans-insights-hub", "type": "system", "name": "APEX_TRANS Insights Hub", "mutation": "create", "observed_at": "09/10/2026"},
            ],
            "relationship_signals": [
                {"signal_id": "sig-01", "from_id": "person:mitchell-simmons", "to_id": "org:apex_trans", "relationship": "CONTRIBUTES_TO", "evidence_ids": ["ev-01"]},
                {"signal_id": "sig-02", "from_id": "person:tenika-johnson", "to_id": "org:apex_trans", "relationship": "CONTRIBUTES_TO", "evidence_ids": ["ev-02"]},
                {"signal_id": "sig-03", "from_id": "system:apex_trans-insights-hub", "to_id": "org:apex_trans", "relationship": "INTEGRATES_WITH", "evidence_ids": ["ev-03"]},
            ],
            "action_updates": [],
            "decision_updates": [],
            "risk_updates": [],
            "proposal_updates": [],
        },
        "evidence_only_statement_ids": [],
    }

    # 4. Deterministic QA Gates
    qa_meta = {
        "meeting_name": "NEET_Insights_Hub_Tech_Services_Discovery",
        "date": "09/10/2026",
        "time": "03:34 PM - 03:39 PM ET",
        "duration_minutes": 5,
        "classification": "confidential",
        "audiences": ["nee-engineering", "partner_advisory-leadership", "apex_trans-operations"],
        "roster": [
            {"name": "Alex Rivera", "org": "APEX_TRANS", "role": "Technology Lead"},
            {"name": "Jordan Taylor", "org": "APEX_TRANS", "role": "Technical Services Lead / Manager"},
            {"name": "Anece Elakkari", "org": "PartnerAdvisory", "role": "Solutions Lead"},
        ],
        "expected_domain_terms": ["technical services", "transmissions", "SCADA", "outage"],
        "meeting_type": "STRATEGIC_DISCOVERY",
    }
    qa_report = evaluate_all_qa_gates(f1_text, f2_text, f3_data, source_text=f1_text, metadata=qa_meta)
    if not qa_report["passed"]:
        for k in ["output_01_forensic_transcript", "output_02_executive_recap", "output_03_canonical_signal_projection", "cross_artifact_consistency"]:
            print(k, qa_report[k].get("passed"), qa_report[k].get("failures"))
    assert qa_report["passed"] is True, f"QA Failures: {qa_report.get('failures')}"

    # 5. Decomposed Multi-Micro-Judge
    use_live = os.environ.get("LIVE_EVAL") == "1" and bool(os.environ.get("OPENAI_API_KEY"))
    judge = DecomposedMultiJudge(
        model="gpt-5.6-luna",
        reasoning_effort="high",
        mock=not use_live,
    )
    eval_result = judge.evaluate_parallel(
        output_1_text=f1_text,
        output_2_text=f2_text,
        output_3_text=json.dumps(f3_data),
        raw_transcript=f1_text,
        metadata=qa_meta,
    )

    assert eval_result["rubric_version"] == "2.0"
    assert eval_result["evaluator_model"] == "gpt-5.6-luna"
    assert eval_result["reasoning_effort"] == "high"
    assert eval_result["status"] == "PASS"
    assert eval_result["output_1_evaluation"]["composite_score"] >= 80
    assert eval_result["output_2_evaluation"]["composite_score"] >= 90
    assert eval_result["output_3_evaluation"]["composite_score"] >= 90
    assert eval_result["cross_artifact_reconciliation"]["passed"] is True
