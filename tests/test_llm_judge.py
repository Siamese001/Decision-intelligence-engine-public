"""
Tests for Wave 3: Decomposed Multi-Micro-Judge & Hardened Rubrics.
Verifies concurrent micro-judge execution, dimension scoring, grounding citations,
and deterministic cross-artifact reconciliation.
"""

import json
from pathlib import Path

from dual_pipeline.llm_judge import (
    CrossArtifactReconciler,
    DecomposedMultiJudge,
    HolisticReleaseJudge,
)


def test_cross_artifact_reconciler_valid_payload():
    reconciler = CrossArtifactReconciler()
    metadata = {
        "date": "08/28/2026",
        "meeting_name": "Project Atlas",
        "prior_entity_ids": ["org:partner_advisory"],
    }
    f3_data = {
        "schema_version": "5.7",
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory", "type": "org", "mutation": "CONFIRM"},
                {"id": "person:dan-okray", "type": "person", "mutation": "CREATE"},
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
    }
    result = reconciler.reconcile("f1 text", "f2 text", f3_data, metadata)
    assert result["status"] == "PASS"
    assert result["passed"] is True
    assert result["total_mutations"] == 2
    assert result["mutation_budget_cleared"] is True


def test_cross_artifact_reconciler_enforces_rules():
    reconciler = CrossArtifactReconciler()

    # Rule 1: Invalid date format (e.g. 2026-08-28 instead of 08/28/2026)
    bad_date_meta = {"date": "2026-08-28"}
    res1 = reconciler.reconcile("", "", {"schema_version": "5.7", "graph_mutations": {}}, bad_date_meta)
    assert res1["status"] == "FAIL"
    assert any("MM/DD/YYYY" in f for f in res1["failures"])

    # Rule 2: Mutation budget exceeded (> 25 mutations)
    oversized_mutations = {
        "schema_version": "5.7",
        "graph_mutations": {
            "entity_upserts": [{"id": f"person:user-{i}", "type": "person", "mutation": "CREATE"} for i in range(26)]
        }
    }
    res2 = reconciler.reconcile("", "", oversized_mutations, {"date": "08/28/2026"})
    assert res2["status"] == "FAIL"
    assert any("exceeds maximum budget" in f for f in res2["failures"])

    # Rule 3: Invalid entity prefix
    bad_prefix_mutations = {
        "schema_version": "5.7",
        "graph_mutations": {
            "entity_upserts": [{"id": "invalid_prefix:something", "type": "other", "mutation": "CREATE"}]
        }
    }
    res3 = reconciler.reconcile("", "", bad_prefix_mutations, {"date": "08/28/2026"})
    assert res3["status"] == "FAIL"
    assert any("does not use an allowed prefix" in f for f in res3["failures"])


SAMPLE_F1_PASSING_TEXT = (
    "# Transcript\n**Drew Oakley** (PartnerAdvisory): [00:00] Hello\n\n---\n\n"
    "## Ambiguity Lock Register\n\n"
    "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
    "|---|---|---|---|---|---|\n"
    "| 1 | Speaker Attribution & Acoustic Grounding | [00:00] | N/A | Drew Oakley (PartnerAdvisory) confirmed | Roster Confirmed |\n"
)


def test_decomposed_multi_judge_parallel_evaluation(tmp_path: Path):
    f1 = tmp_path / "01_transcript.md"
    f2 = tmp_path / "02_recap.md"
    f3 = tmp_path / "03_graph.json"

    f1.write_text(SAMPLE_F1_PASSING_TEXT, encoding="utf-8")
    f2.write_text("Subject: Recap\n## Meeting Objective\nTest", encoding="utf-8")
    f3.write_text(
        json.dumps({
            "schema_version": "5.7",
            "meeting_id": "test",
            "graph_mutations": {
                "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "mutation": "CONFIRM"}]
            }
        }),
        encoding="utf-8"
    )

    judge = DecomposedMultiJudge(mock=True)
    report = judge.evaluate_parallel(f1, f2, f3, metadata={"date": "08/28/2026"})

    assert report["status"] == "PASS"
    assert report["evaluation_mode"] == "DECOMPOSED_CONCURRENT"
    assert report["output_1_evaluation"]["composite_score"] >= 95
    assert report["output_2_evaluation"]["composite_score"] >= 95
    assert report["output_3_evaluation"]["composite_score"] >= 95
    assert report["cross_artifact_reconciliation"]["status"] == "PASS"


def test_decomposed_multi_judge_parallel_evaluation_string_kwargs():
    f1_text = SAMPLE_F1_PASSING_TEXT
    f2_text = "Subject: Recap\n## Meeting Objective\nTest"
    f3_text = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "test",
        "graph_mutations": {
            "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "mutation": "CONFIRM"}]
        }
    })

    judge = DecomposedMultiJudge(mock=True)
    report = judge.evaluate_parallel(
        output_1_text=f1_text,
        output_2_text=f2_text,
        output_3_text=f3_text,
        raw_transcript="raw audio text",
        metadata={"date": "08/28/2026"}
    )

    assert report["status"] == "PASS"
    assert report["evaluation_mode"] == "DECOMPOSED_CONCURRENT"
    assert report["output_1_evaluation"]["composite_score"] >= 95
    assert report["cross_artifact_reconciliation"]["status"] == "PASS"


def test_executive_recap_passing_threshold_95(monkeypatch):
    from dual_pipeline.release_thresholds import ReleaseThresholdPolicy
    policy_95 = ReleaseThresholdPolicy(output1_pass_threshold=95, output2_pass_threshold=95, output3_pass_threshold=95)
    monkeypatch.setattr("dual_pipeline.release_thresholds.CANONICAL_RELEASE_POLICY", policy_95)
    monkeypatch.setattr("dual_pipeline.llm_judge_core.orchestrator.CANONICAL_RELEASE_POLICY", policy_95)
    judge = DecomposedMultiJudge(mock=True)

    # When s2 is 94, report status must be FAIL
    def mock_eval_94(*args, **kwargs):
        return {
            "composite_score": 94,
            "verdict": "FAIL",
            "dimension_1_executive_altitude": 23,
            "dimension_2_technical_substance": 23,
            "dimension_3_strategic_synthesis": 24,
            "dimension_4_scoping_and_modality": 24,
        }

    monkeypatch.setattr(judge.judge_recap, "evaluate", mock_eval_94)
    report = judge.evaluate_parallel(
        output_1_text=SAMPLE_F1_PASSING_TEXT,
        output_2_text="Subject: Recap\n## Meeting Objective\nTest",
        output_3_text=json.dumps({
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "mutation": "CONFIRM"}]
            },
        }),
        metadata={"date": "08/28/2026"},
    )
    assert report["status"] == "FAIL"

    # When s2 is 95, report status must be PASS
    def mock_eval_95(*args, **kwargs):
        return {
            "composite_score": 95,
            "verdict": "PASS",
            "dimension_1_executive_altitude": 24,
            "dimension_2_technical_substance": 23,
            "dimension_3_strategic_synthesis": 24,
            "dimension_4_scoping_and_modality": 24,
        }

    monkeypatch.setattr(judge.judge_recap, "evaluate", mock_eval_95)
    report2 = judge.evaluate_parallel(
        output_1_text=SAMPLE_F1_PASSING_TEXT,
        output_2_text="Subject: Recap\n## Meeting Objective\nTest",
        output_3_text=json.dumps({
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "mutation": "CONFIRM"}]
            },
        }),
        metadata={"date": "08/28/2026"},
    )
    assert report2["status"] == "PASS"


def test_canonical_signal_projection_passing_threshold_95(monkeypatch):
    """Verify Output 3 strictly requires composite_score >= 95 to pass."""
    from dual_pipeline.release_thresholds import ReleaseThresholdPolicy
    policy_95 = ReleaseThresholdPolicy(output1_pass_threshold=95, output2_pass_threshold=95, output3_pass_threshold=95)
    monkeypatch.setattr("dual_pipeline.release_thresholds.CANONICAL_RELEASE_POLICY", policy_95)
    monkeypatch.setattr("dual_pipeline.llm_judge_core.orchestrator.CANONICAL_RELEASE_POLICY", policy_95)
    judge = DecomposedMultiJudge(mock=True)

    # When s3 is 94, report status must be FAIL
    def mock_eval_94(*args, **kwargs):
        return {
            "composite_score": 94,
            "verdict": "FAIL",
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 14,
        }

    monkeypatch.setattr(judge.judge_graph, "evaluate", mock_eval_94)
    report = judge.evaluate_parallel(
        output_1_text=SAMPLE_F1_PASSING_TEXT,
        output_2_text="Subject: Recap\n## Meeting Objective\nTest",
        output_3_text=json.dumps({
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "mutation": "CONFIRM"}]
            },
        }),
        metadata={"date": "08/28/2026"},
    )
    assert report["status"] == "FAIL"
    assert report["output_3_evaluation"]["verdict"] == "FAIL"

    # When s3 is 95, report status must be PASS
    def mock_eval_95(*args, **kwargs):
        return {
            "composite_score": 95,
            "verdict": "PASS",
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 15,
        }

    monkeypatch.setattr(judge.judge_graph, "evaluate", mock_eval_95)
    report2 = judge.evaluate_parallel(
        output_1_text=SAMPLE_F1_PASSING_TEXT,
        output_2_text="Subject: Recap\n## Meeting Objective\nTest",
        output_3_text=json.dumps({
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "mutation": "CONFIRM"}]
            },
        }),
        metadata={"date": "08/28/2026"},
    )
    assert report2["status"] == "PASS"
    assert report2["output_3_evaluation"]["verdict"] == "PASS"


def test_holistic_release_judge_mock():
    judge = HolisticReleaseJudge(mock=True)
    assert judge.model in ("gpt-5.6-luna", "gpt-5.6-luna", "gpt-5.6-sol")
    assert judge.reasoning_effort in ("high", "medium", "xhigh")

    result = judge.evaluate(
        output_1_text="# Transcript\n[00:01:00] Alex Sanchez: Core system online.",
        output_2_text="# Executive Recap\nDate: 08/28/2026\nObjective: System verify.",
        output_3_text=json.dumps({
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [{"id": "system:nexus", "type": "system", "mutation": "CONFIRM"}],
                "action_updates": [],
            },
        }),
        metadata={"date": "08/28/2026"},
    )
    assert result["status"] == "PASS"
    assert result["evaluation_mode"] == "HOLISTIC_UNIFIED"
    assert result["composite_score"] >= 95
    assert result["evaluator_model"] in ("gpt-5.6-luna", "gpt-5.6-luna", "gpt-5.6-sol")
    assert result["reasoning_effort"] in ("high", "medium", "xhigh")


def test_holistic_release_judge_c19_firewall():
    judge = HolisticReleaseJudge(mock=True)
    # Output 02 contains prohibited career leverage / internal advisory marker
    result = judge.evaluate(
        output_1_text="# Transcript\n[00:01:00] Alex Sanchez: Core system online.",
        output_2_text="# Executive Recap\nDate: 08/28/2026\nTargeting executive compensation band and networking_wedge for Tanner.",
        output_3_text=json.dumps({"schema_version": "5.7", "graph_mutations": {}}),
        metadata={"date": "08/28/2026"},
    )
    assert result["status"] == "FAIL"
    assert result["composite_score"] == 30
    assert "RULE C-19 VIOLATION" in result["forensic_rationale"]


def test_decomposed_multi_judge_delegates_to_holistic():
    judge = DecomposedMultiJudge(mock=True)
    report = judge.evaluate_parallel(
        output_1_text="# Transcript\n**Drew Oakley**: Hello",
        output_2_text="Subject: Recap\n## Meeting Objective\nTest",
        output_3_text=json.dumps({
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "mutation": "CONFIRM"}]
            },
        }),
        metadata={"date": "08/28/2026"},
        mode="holistic",
    )
    assert report["evaluation_mode"] == "HOLISTIC_UNIFIED"
    assert report["status"] == "PASS"
    assert report["evaluator_model"] in ("gpt-5.6-luna", "gpt-5.6-sol")


