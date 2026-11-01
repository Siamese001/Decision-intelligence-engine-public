"""tests/test_incremental_graph_reconciliation.py

Proves Feature 2 (Part B):
When re-running updated inputs, only incremental additions (if anything material)
are reflected in the graph, keeping the graph uncluttered and high-signal:
1. Entities existing in prior projection are reconciled from 'CREATE' to 'CONFIRM'.
2. Previously established relationship edges are pruned so duplicate edges are not emitted.
3. Non-material modifications (same entities and signals) result in zero new mutating clutter.
4. Net-new entities and relationships introduced in updated inputs are accurately added.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_stages_core.incremental_reconciler import (
    reconcile_incremental_projection,
)


def test_reconciler_confirms_existing_and_creates_new():
    """Unit test for reconcile_incremental_projection verifying entity and signal pruning."""
    prior = {
        "schema_version": "5.7",
        "meeting_id": "nexus-01",
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CREATE"},
                {"id": "workstream:lens_phase_1", "type": "workstream", "name": "LENS Phase 1", "mutation": "CREATE"},
            ],
            "relationship_signals": [
                {"signal_id": "sig-01", "from_id": "person:amit_ayer", "to_id": "workstream:lens_phase_1", "relationship": "GOVERNS"},
            ],
            "decision_updates": [
                {"decision_id": "dec-01", "statement": "Phase 1 acceptance criteria approved."},
            ],
            "risk_updates": [],
        },
    }

    current = {
        "schema_version": "5.7",
        "meeting_id": "nexus-01",
        "graph_mutations": {
            "entity_upserts": [
                # Both already exist in prior, but emitted as CREATE by model
                {"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CREATE"},
                {"id": "workstream:lens_phase_1", "type": "workstream", "name": "LENS Phase 1", "mutation": "CREATE"},
                # Genuinely new entity introduced in update
                {"id": "person:ed_moore", "type": "person", "name": "Ed Moore", "mutation": "CREATE"},
            ],
            "relationship_signals": [
                # Duplicate edge from prior run
                {"signal_id": "sig-01-dup", "from_id": "person:amit_ayer", "to_id": "workstream:lens_phase_1", "relationship": "GOVERNS"},
                # Net-new edge connecting the new entity
                {"signal_id": "sig-02-new", "from_id": "person:ed_moore", "to_id": "workstream:lens_phase_1", "relationship": "CONTRIBUTES_TO"},
            ],
            "decision_updates": [
                # Exact duplicate decision
                {"decision_id": "dec-01", "statement": "Phase 1 acceptance criteria approved."},
                # Net-new decision
                {"decision_id": "dec-02", "statement": "Targeting 50 to 70 asset validation cohort."},
            ],
            "risk_updates": [],
        },
    }

    reconciled = reconcile_incremental_projection(current, prior_projection=prior)
    mutations = reconciled["graph_mutations"]

    # 1. Existing entities must be CONFIRM, net-new must be CREATE
    ent_map = {e["id"]: e for e in mutations["entity_upserts"]}
    assert ent_map["person:amit_ayer"]["mutation"] == "CONFIRM"
    assert ent_map["workstream:lens_phase_1"]["mutation"] == "CONFIRM"
    assert ent_map["person:ed_moore"]["mutation"] == "CREATE"

    # 2. Duplicate signal must be pruned; net-new signal retained
    sigs = mutations["relationship_signals"]
    assert len(sigs) == 1
    assert sigs[0]["from_id"] == "person:ed_moore"
    assert sigs[0]["to_id"] == "workstream:lens_phase_1"
    assert sigs[0]["relationship"] == "CONTRIBUTES_TO"

    # 3. Duplicate decision pruned; net-new decision kept
    decs = mutations["decision_updates"]
    assert len(decs) == 1
    assert decs[0]["decision_id"] == "dec-02"

    # 4. Telemetry verifies incremental delta
    telemetry = reconciled["_strategic_analysis"]["_incremental_reconciliation"]
    assert telemetry["confirmed_entities_count"] == 2
    assert telemetry["incremental_entities_added"] == 1
    assert telemetry["incremental_signals_added"] == 1
    assert telemetry["is_material_graph_change"] is True


def test_non_material_input_produces_zero_mutating_clutter():
    """Verifies that re-running when no new entities/signals are present emits zero mutating clutter."""
    prior = {
        "schema_version": "5.7",
        "meeting_id": "nexus-02",
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CREATE"},
            ],
            "relationship_signals": [
                {"signal_id": "sig-01", "from_id": "person:amit_ayer", "to_id": "org:partner_advisory", "relationship": "REPRESENTS"},
            ],
            "decision_updates": [],
            "risk_updates": [],
        },
    }

    # Current has the exact same entity and signal with no net-new concepts
    current = {
        "schema_version": "5.7",
        "meeting_id": "nexus-02",
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CREATE"},
            ],
            "relationship_signals": [
                {"signal_id": "sig-01", "from_id": "person:amit_ayer", "to_id": "org:partner_advisory", "relationship": "REPRESENTS"},
            ],
            "decision_updates": [],
            "risk_updates": [],
        },
    }

    reconciled = reconcile_incremental_projection(current, prior_projection=prior)
    mutations = reconciled["graph_mutations"]

    assert len(mutations["entity_upserts"]) == 1
    assert mutations["entity_upserts"][0]["mutation"] == "CONFIRM"
    # Zero duplicate signals emitted
    assert len(mutations["relationship_signals"]) == 0

    telemetry = reconciled["_strategic_analysis"]["_incremental_reconciliation"]
    assert telemetry["incremental_entities_added"] == 0
    assert telemetry["incremental_signals_added"] == 0
    assert telemetry["is_material_graph_change"] is False


def test_pipeline_e2e_incremental_graph_generation(tmp_path):
    """End-to-end pipeline test verifying that an updated transcript produces an incremental Output 03."""
    output_dir = tmp_path / "project_nexus" / "09-16-2026"

    metadata = {
        "meeting_name": "Nexus Graph Review",
        "date": "09-16-2026",
        "time": "03:00 PM",
        "project_name": "project_nexus",
        "roster": ["Amit Ayer", "Cameron Mercer"],
    }

    pipeline = RawTranscriptPipeline()

    with patch("dual_pipeline.transcript_stages_core.l1_cognition.L1ForensicCognition.plan") as mock_l1, \
         patch("dual_pipeline.transcript_stages_core.l2_executors.L2ForensicNormalizerExecutor.execute") as mock_l2, \
         patch("dual_pipeline.transcript_stages_core.x_gates.XExitGatesEvaluator.execute") as mock_xgates, \
         patch("dual_pipeline.transcript_pipeline_core.pipeline.evaluate_judge_and_release") as mock_judge, \
         patch("dual_pipeline.transcript_pipeline_core.post_l2_review.TranscriptL1PostExecutionReviewer.review_execution") as mock_rev, \
         patch("dual_pipeline.transcript_pipeline_core.release_runner.prepare_package") as mock_prepare:

        mock_l1.return_value = MagicMock(plan_id="plan-p3", source_type="mock", diarization_status="NATIVE")
        mock_l2.return_value = "# Meeting -- HITL-Resolved Transcript\n\nDialogue."
        mock_rev.return_value = MagicMock()
        mock_rev.return_value.to_dict.return_value = {"status": "PASS", "passed": True}
        mock_xgates.return_value = True

        def mock_prepare_side_effect(*args, **kwargs):
            out = kwargs.get("output_path")
            if out:
                Path(out).write_text(json.dumps({"contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0"}))
            return {}

        mock_prepare.side_effect = mock_prepare_side_effect

        mock_judge.return_value = (
            {"status": "PASS", "passed": True},
            {"status": "PASS"},
            {
                "status": "PASS",
                "authoritative_write_authorized": True,
                "output_1_pass": True,
                "output_2_pass": True,
                "output_3_pass": True,
                "reasoning": "Passing release.",
            },
        )

        # Mock Stage 03 synthesizer to return initial graph in run 1, and extended graph in run 2
        run1_graph = json.dumps({
            "schema_version": "5.7",
            "meeting_id": "nexus-graph-01",
            "evidence_index": {
                "ev-01": {"evidence_id": "ev-01", "text": "Initial sync.", "exact_text": "Initial sync."}
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CREATE", "evidence_ids": ["ev-01"]},
                    {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "mutation": "CREATE", "evidence_ids": ["ev-01"]},
                ],
                "relationship_signals": [
                    {"signal_id": "sig-01", "from_id": "person:amit_ayer", "to_id": "org:partner_advisory", "relationship": "REPRESENTS", "evidence_ids": ["ev-01"]},
                ],
                "decision_updates": [],
                "risk_updates": [],
                "proposal_updates": [],
                "action_updates": [],
            },
        })

        run2_graph = json.dumps({
            "schema_version": "5.7",
            "meeting_id": "nexus-graph-01",
            "evidence_index": {
                "ev-01": {"evidence_id": "ev-01", "text": "Initial sync.", "exact_text": "Initial sync."},
                "ev-02": {"evidence_id": "ev-02", "text": "Added Project Nexus.", "exact_text": "Added Project Nexus."},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CREATE", "evidence_ids": ["ev-01"]},
                    {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "mutation": "CREATE", "evidence_ids": ["ev-01"]},
                    {"id": "project:project_nexus", "type": "project", "name": "Project Nexus", "mutation": "CREATE", "evidence_ids": ["ev-02"]},
                ],
                "relationship_signals": [
                    # Duplicate edge from run 1
                    {"signal_id": "sig-01", "from_id": "person:amit_ayer", "to_id": "org:partner_advisory", "relationship": "REPRESENTS", "evidence_ids": ["ev-01"]},
                    # Net-new edge connecting the new entity
                    {"signal_id": "sig-02", "from_id": "person:amit_ayer", "to_id": "project:project_nexus", "relationship": "GOVERNS", "evidence_ids": ["ev-02"]},
                ],
                "decision_updates": [],
                "risk_updates": [],
                "proposal_updates": [],
                "action_updates": [],
            },
        })

        call_idx = [0]
        def mock_synthesize_graph(*args, **kwargs):
            call_idx[0] += 1
            return run1_graph if call_idx[0] == 1 else run2_graph

        with patch.object(pipeline.l2_graph.provider, "synthesize_graph", side_effect=mock_synthesize_graph):
            # --- RUN 1 ---
            t1 = tmp_path / "t1.md"
            t1.write_text("Speaker 1: Initial sync.", encoding="utf-8")
            res1 = pipeline.run(input_path=str(t1), metadata=metadata.copy(), output_dir=str(output_dir), max_judge_retries=0)
            g1_data = json.loads(Path(res1["03_graph"]).read_text(encoding="utf-8"))
            assert len(g1_data["graph_mutations"]["entity_upserts"]) == 2
            assert all(e["mutation"] == "CREATE" for e in g1_data["graph_mutations"]["entity_upserts"])

            # --- RUN 2 (Incremental) ---
            t2 = tmp_path / "t2.md"
            t2.write_text("Speaker 1: Initial sync.\nSpeaker 2: Added Project Nexus.", encoding="utf-8")
            res2 = pipeline.run(input_path=str(t2), metadata=metadata.copy(), output_dir=str(output_dir), max_judge_retries=0)
            g2_data = json.loads(Path(res2["03_graph"]).read_text(encoding="utf-8"))

            ent_map = {e["id"]: e for e in g2_data["graph_mutations"]["entity_upserts"]}
            # Existing entities confirmed; net-new entity created!
            assert ent_map["person:amit_ayer"]["mutation"] == "CONFIRM"
            assert ent_map["org:partner_advisory"]["mutation"] == "CONFIRM"
            assert ent_map["project:project_nexus"]["mutation"] == "CREATE"
            # Prior signal pruned, only 1 net-new relationship signal emitted!
            assert len(g2_data["graph_mutations"]["relationship_signals"]) == 1
            assert g2_data["graph_mutations"]["relationship_signals"][0]["relationship"] == "GOVERNS"
