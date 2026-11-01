"""Wave 8: End-to-End Best-in-Class Decision Intelligence Knowledge Graph Verification Suite.

Verifies all 8 architectural layers end to end on isolated SQLite sandboxes and read-only
graph snapshots:
1. Typed Ontology & Computable Constraint Parameters
2. Multi-Hop Counterfactual & Causal Traversal
3. Quantitative Funnel, Capacity & Rate Math Engine
4. Temporal & Lifecycle Horizon Awareness
5. Dialectical Contradiction & Tension Resolution
6. Constraint-Satisfaction Option Synthesis & Devil's Advocate Stress Testing
7. Stakeholder-Calibrated Next Best Actions & Thought Leadership
8. End-to-End X1 Answer Validation & Read-Only Graph Immutability Proof
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import connect_sqlite, digest, sha256_file
from dual_pipeline.decision_engine import (
    build_decision_evidence_pack,
    build_decision_frame,
    evaluate_decision,
)
from dual_pipeline.decision_engine_core.option_synthesizer import (
    HYBRID_MITIGATED_OPTION_LABEL,
)
from dual_pipeline.decision_engine_core.quantitative_math import (
    compute_commercial_burn_math,
    compute_portfolio_absorption_math,
    compute_recruiting_funnel_math,
    evaluate_temporal_constraint_breaches,
    parse_temporal_horizon,
)
from dual_pipeline.decision_witness import DecisionWitnessEngine
from dual_pipeline.graph_retrieval_core.recursive_traversal import traverse_k_hop_neighborhood
from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.query_core.anti_conflation import validate_answer_anti_conflation
from dual_pipeline.query_core.dynamic_entities import resolve_named_coverage_subjects
from dual_pipeline.query_core.reasoning import reason_over_packet, validate_answer
from dual_pipeline.query_core.thought_leadership import (
    extract_audience_stakeholders,
    strip_audience_clause,
)
from dual_pipeline.strategic_reasoning_core.channels import build_strategic_channels
from dual_pipeline.strategic_reasoning_core.profiling import (
    answer_use_for_profile,
    strategic_profile_for_record,
)
from dual_pipeline.strategic_reasoning_core.quantitative_ontology import (
    classify_constraint_category,
    extract_quantitative_constraints,
    infer_object_literal_type,
    is_constraint_claim,
)

ROOT = Path(__file__).resolve().parents[1]


class BestInClassGraphEndToEndTests(unittest.TestCase):
    def _create_wpb_sandbox_db(self, db_path: Path) -> InMemoryGraphTopology:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(
                """
                CREATE TABLE node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    display_name TEXT,
                    node_type TEXT NOT NULL,
                    description TEXT,
                    status TEXT DEFAULT 'active',
                    active INTEGER DEFAULT 1
                );
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    legacy_predicate TEXT,
                    object_node_id TEXT,
                    object_literal TEXT,
                    object_literal_type TEXT,
                    scope_text TEXT,
                    assertion_type TEXT DEFAULT 'fact',
                    atomicity_state TEXT DEFAULT 'atomic',
                    canonical_status TEXT DEFAULT 'canonical',
                    canonical_eligible INTEGER DEFAULT 1,
                    currentness_state TEXT DEFAULT 'current',
                    retrieval_eligible INTEGER DEFAULT 1,
                    materiality TEXT DEFAULT 'high',
                    confidence_label TEXT DEFAULT 'high',
                    classification TEXT DEFAULT 'internal',
                    allowed_audiences_json TEXT DEFAULT '["partner_advisory_consulting", "nee-engineering"]',
                    hitl_required INTEGER DEFAULT 0,
                    origin_type TEXT DEFAULT 'legacy_migration',
                    superseded_by_assertion_id TEXT
                );
                CREATE TABLE assertion_evidence (
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    evidence_relationship TEXT NOT NULL
                );
                CREATE VIEW v_canonical_edge AS
                SELECT a.assertion_id,
                       a.subject_node_id AS from_node_id,
                       'RELATES_TO' AS relationship_family,
                       a.predicate AS edge_type,
                       a.object_node_id AS to_node_id,
                       a.materiality,
                       'test_source' AS source_id,
                       a.hitl_required
                  FROM assertion a
                 WHERE a.object_node_id IS NOT NULL;
                """
            )
            nodes = [
                ("entity_accelerate_power", "Accelerate Power", "Accelerate Power", "entity", 1),
                ("person_chakra", "Chakra Sankaraiah", "Chakra Sankaraiah", "person", 1),
                ("person_amit", "Amit Ayer", "Amit Ayer", "person", 1),
                ("node_nee_bar_constraint", "NEE Qualification Bar Constraint", "Qualification Bar", "constraint", 1),
            ]
            conn.executemany(
                "INSERT INTO node (node_id, canonical_name, display_name, node_type, active) VALUES (?, ?, ?, ?, ?)",
                nodes,
            )

            assertions = [
                (
                    "asrt_acc_pitch",
                    "entity_accelerate_power",
                    "HAS_CLAIM_TEXT",
                    None,
                    "Accelerate Power proposes forward deployed teams billing 42 hours a week at six hundred thousand per quarter.",
                    "text",
                    "Accelerate Power proposes forward deployed teams billing 42 hours a week at six hundred thousand per quarter.",
                ),
                (
                    "asrt_chakra_pods",
                    "person_chakra",
                    "HAS_CLAIM_TEXT",
                    None,
                    "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
                    "numeric_ratio",
                    "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
                ),
                (
                    "asrt_amit_bar",
                    "person_amit",
                    "GOVERNS",
                    "node_nee_bar_constraint",
                    "Calibrate candidate screening against NEA 5%-10% qualification bar and pass rate.",
                    "percentage",
                    "Calibrate candidate screening against NEA 5%-10% qualification bar and pass rate.",
                ),
            ]
            conn.executemany(
                """INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, object_node_id,
                    object_literal, object_literal_type, scope_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                assertions,
            )
            conn.executemany(
                "INSERT INTO assertion_evidence VALUES (?, ?, 'supports')",
                [
                    ("asrt_acc_pitch", "ev_acc_pitch"),
                    ("asrt_chakra_pods", "ev_chakra_pods"),
                    ("asrt_amit_bar", "ev_amit_bar"),
                ],
            )
            conn.commit()
        finally:
            conn.close()
        return InMemoryGraphTopology.load_from_sqlite(db_path)

    def test_all_eight_best_in_class_layers_end_to_end(self) -> None:
        question = (
            "Provide honest analysis of Brad and Beau discussion on pods local to "
            "West Palm Beach area from the perspective of NEE in Q2 2026. "
            "Audience Dan and Stephan who are intimately familiar with topic. "
            "Need leadership creative POV on what might work and what will not work and explain why."
        )

        # Layer 1: Typed Ontology & Computable Constraint Parameters
        bar_text = "Calibrate candidate screening against NEA 5%-10% qualification bar and 7 years experience."
        self.assertTrue(is_constraint_claim(bar_text))
        self.assertEqual(classify_constraint_category(bar_text), "talent_bar")
        self.assertEqual(infer_object_literal_type(bar_text), "percentage")
        bar_params = extract_quantitative_constraints(bar_text)
        self.assertTrue(any(p["metric_type"] == "pass_rate_pct" for p in bar_params))

        # Layer 2: Multi-Hop Counterfactual & Causal Traversal + Witness Subgraph
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "wpb_sandbox.sqlite"
            topology = self._create_wpb_sandbox_db(db_path)
            traversal = traverse_k_hop_neighborhood(
                ["entity_accelerate_power"],
                max_hops=2,
                graph_path=db_path,
                include_constraint_bridges=True,
            )
            self.assertIn("person_chakra", traversal["nodes"])
            self.assertIn("person_amit", traversal["nodes"])
            edge_types = {e["edge_type"] for e in traversal["edges"]}
            self.assertIn("CONSTRAINS_CAPACITY_OF", edge_types)
            self.assertIn("GOVERNED_BY_STANDARD", edge_types)

            # Layer 3: Quantitative Funnel, Capacity & Rate Math Engine
            funnel = compute_recruiting_funnel_math(
                proposed_headcount=50.0,
                pass_rate_pct=7.5,
                evidence_id="ev_amit_bar",
            )
            self.assertEqual(funnel["required_candidate_pool"], 667)
            self.assertEqual(funnel["feasibility_status"], "BREACH")

            cap = compute_portfolio_absorption_math(
                proposed_headcount=50.0,
                active_workstreams=4.0,
                engineers_per_workstream=6.0,
                evidence_id="ev_chakra_pods",
            )
            self.assertEqual(cap["max_absorbable_headcount"], 24.0)
            self.assertEqual(cap["capacity_overrun_headcount"], 26.0)
            self.assertEqual(cap["feasibility_status"], "BREACH")

            burn = compute_commercial_burn_math(
                pod_count=10.0,
                quarterly_rate_usd=600_000.0,
                weekly_hours=42.0,
                onshore_premium_pct=45.0,
                evidence_id="ev_acc_pitch",
            )
            self.assertEqual(burn["quarterly_burn_usd"], 6_000_000.0)
            self.assertEqual(burn["excess_weekly_hours"], 2.0)

            # Layer 4: Temporal & Lifecycle Horizon Awareness
            horizon_meta = parse_temporal_horizon("Q2 2026")
            self.assertEqual(horizon_meta["quarter"], "Q2 2026")
            self.assertEqual(horizon_meta["horizon_days"], 90)
            frame = build_decision_frame(question=question)
            self.assertEqual(frame["frame_state"], "READY")
            self.assertEqual(frame["time_horizon"], "Q2 2026")
            self.assertEqual(
                frame["extensions"]["com.partner_advisory.nee.temporal-horizon"]["horizon_days"],
                90,
            )

            breaches = evaluate_temporal_constraint_breaches(
                option_label="Blocked / Non-Viable Elements: Immediate Staggered SOW Local Pods",
                bound_records=[],
                constraints=[
                    {
                        "record_id": "rec_deprov_14d",
                        "title": "IT Deprovisioning Notice Window",
                        "text": "Mandatory two weeks prior (14-day) IT deprovisioning notice window.",
                        "evidence_ids": ["ev_deprov_1"],
                        "constraint_category": "schedule_notice",
                    }
                ],
                time_horizon="30 days",
            )
            self.assertEqual(len(breaches), 1)
            self.assertEqual(breaches[0]["notice_window_days"], 14)

            # Build full Evidence Packet & Strategic Channels
            seed_item = {
                "record": {
                    "record_id": "assertion:asrt_acc_pitch",
                    "title": "Accelerate Power West Palm Beach 10-Pod Proposal",
                    "retrieval_text": (
                        "Brad Jackson and Beau Williamson discussed deploying 10 local pods "
                        "(50 full-stack engineers) in West Palm Beach under Accelerate Power "
                        "billing 42 hours/week at $600k per quarter."
                    ),
                    "record_type": "canonical_assertion",
                    "lane": "approved",
                    "evidence_ids": ["ev_acc_pitch"],
                    "currentness_state": "current",
                },
                "retrieval_metadata": {"answer_use": "factual_answer"},
            }
            constraint_rows = [
                (
                    "assertion:asrt_chakra_pods",
                    "NEA 1:8 anchor ratio and 4 active workstreams (5-6 engineers per pod)",
                    "NEA standardizes on a 1:8 lead-to-engineer ratio across 4 active workstreams (5-6 engineers per pod), phasing out segregated pods.",
                    "ev_chakra_pods",
                ),
                (
                    "assertion:asrt_amit_bar",
                    "NEA 5-10% technical qualification bar and 14-day deprovisioning notice",
                    "NEE enforces a 5-10% technical screening pass rate, faces a 45% higher onshore rate differential, and requires two weeks prior (14-day) IT deprovisioning notice.",
                    "ev_amit_bar",
                ),
            ]
            approved_items = [seed_item]
            graph_paths = []
            for rec_id, title, text, ev_id in constraint_rows:
                prof = strategic_profile_for_record(
                    record_type="canonical_assertion",
                    lane="approved",
                    classification="internal",
                    text=text,
                )
                use = answer_use_for_profile(
                    base_answer_use="factual_answer",
                    profile=prof,
                    lane="approved",
                )
                approved_items.append(
                    {
                        "record": {
                            "record_id": rec_id,
                            "title": title,
                            "retrieval_text": text,
                            "record_type": "canonical_assertion",
                            "lane": "approved",
                            "evidence_ids": [ev_id],
                            "currentness_state": "current",
                            "strategic_profile": prof,
                            "answer_use": use,
                        },
                        "retrieval_metadata": {"answer_use": use},
                    }
                )
                graph_paths.append(
                    {
                        "record_id": rec_id,
                        "seed_record_id": "assertion:asrt_acc_pitch",
                        "hop_count": 2,
                        "steps": [
                            {
                                "from_node_id": "entity_accelerate_power",
                                "to_node_id": rec_id,
                                "relationship_family": "SHARES_CAPABILITY_DOMAIN",
                            }
                        ],
                    }
                )

            packet = {
                "graph_snapshot": "snap-wave8-e2e",
                "allowed_lanes": ["approved"],
                "approved": approved_items,
                "candidate": [],
                "external_public": [],
                "counterevidence": [],
                "gaps": [],
                "exclusions": [],
                "graph_paths": graph_paths,
            }
            packet["packet_digest"] = digest(
                {k: v for k, v in packet.items() if k != "packet_digest"}
            )
            channels = build_strategic_channels(packet)
            self.assertEqual(channels["role_counts"].get("constraint"), 2)

            # Layer 5: Dialectical Contradiction & Tension Resolution
            evidence_pack = build_decision_evidence_pack(
                query_result={
                    "run_id": "run-wave8-e2e",
                    "question": question,
                    "generation_id": "gen-wave8",
                    "graph_snapshot": "snap-wave8-e2e",
                    "graph_sha256_after": "a" * 64,
                    "embedding_backend": "bge-m3",
                    "bm25_executed": True,
                    "dense_executed": True,
                    "sparse_executed": True,
                    "colbert_executed": True,
                    "graph_traversal_executed": True,
                },
                evidence_packet=packet,
                retrieval_report={
                    "terminal_coverage_disposition": "covered",
                    "coverage_counts": {"covered": 3},
                },
            )
            self.assertGreaterEqual(len(evidence_pack["conflicts"]), 1)
            tension = evidence_pack["conflicts"][0]
            self.assertEqual(tension["conflict_type"], "dialectical_constraint_tension")

            # Verify witness subgraph preserves constraint seeds
            witness = DecisionWitnessEngine.build_witness_subgraph(
                decision_frame=frame,
                evidence_pack={
                    "source_evidence_ids": ["ev_acc_pitch", "ev_chakra_pods", "ev_amit_bar"],
                    "constraints": [
                        {
                            "record_id": "assertion:asrt_chakra_pods",
                            "title": "Chakra Sankaraiah pod ratio",
                            "summary": "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
                            "evidence_id": "ev_chakra_pods",
                        },
                        {
                            "record_id": "assertion:asrt_amit_bar",
                            "title": "NEE Qualification Bar Constraint",
                            "summary": "5%-10% qualification bar pass rate.",
                            "evidence_id": "ev_amit_bar",
                        },
                    ],
                },
                topology=topology,
                active_pair={
                    "generation_id": "gen-wave8-e2e",
                    "graph_sha256": topology.graph_sha256,
                    "sidecar_sha256": "0" * 64,
                },
                options=frame["options"],
                assessments=[],
            )
            validate_artifact(witness)
            self.assertIn(
                "node_nee_bar_constraint",
                witness["extensions"]["com.partner_advisory.nee.constraint-seed-node-ids"],
            )

            # Layer 7: Stakeholder-Calibrated Next Best Actions & Thought Leadership
            stakeholders = extract_audience_stakeholders(question)
            self.assertEqual(stakeholders, ["Dan", "Stephan"])
            stripped_q = strip_audience_clause(question)
            self.assertIn("Brad", stripped_q)
            self.assertIn("Beau", stripped_q)

            plan = {
                "question": question,
                "query_type": "opportunity_nba",
                "retrieval_mode": "strategic",
                "allowed_lanes": ["approved"],
                "primary_entities": ["Brad Jackson", "Beau Williamson", "Dan", "Stephan"],
                "dynamic_entities": [
                    {"canonical_name": "Brad Jackson"},
                    {"canonical_name": "Beau Williamson"},
                    {"canonical_name": "Drew Oakley"},
                    {"canonical_name": "Cameron Mercer"},
                ],
            }
            named_subjects = resolve_named_coverage_subjects(question, plan, active=None)
            self.assertIn("Brad Jackson", named_subjects)
            self.assertIn("Beau Williamson", named_subjects)
            self.assertNotIn("Drew Oakley", named_subjects)
            self.assertNotIn("Cameron Mercer", named_subjects)

            answer = reason_over_packet(plan, packet)
            x1 = validate_answer(answer, packet)
            self.assertEqual(x1["status"], "PASS", msg=f"X1 failed: {x1}")
            anti_conf = validate_answer_anti_conflation(answer)
            self.assertEqual(anti_conf, [], msg=f"Anti-conflation failed: {anti_conf}")
            self.assertGreaterEqual(len(answer["next_best_actions"]), 3)
            nba_text = " ".join(r["action"] for r in answer["next_best_actions"])
            self.assertTrue(
                any(tok in nba_text for tok in ("667", "1:8", "40-hour", "14-day", "Dan", "Stephan")),
                msg=f"Expected quantitative thought-leadership NBA, got: {nba_text}",
            )

            # Layer 6: Constraint-Satisfaction Option Synthesis & Devil's Advocate
            evaluation = evaluate_decision(
                frame=frame,
                evidence_pack=evidence_pack,
                answer=answer,
                execution={"query_run_id": "run-wave8-e2e"},
            )
            self.assertEqual(evaluation["terminal_state"], "DECISION_PRODUCED")
            self.assertIsNotNone(evaluation["recommended_option"])
            self.assertEqual(
                evaluation["recommended_option"]["label"],
                HYBRID_MITIGATED_OPTION_LABEL,
            )
            self.assertEqual(
                evaluation["recommended_option"]["source"],
                "constraint_satisfaction_synthesis",
            )
            self.assertGreaterEqual(
                len(evaluation["recommended_option"]["parameter_mutations"]),
                2,
            )
            da_ext = evaluation["extensions"]["com.partner_advisory.nee.counterfactual-challenge"]
            self.assertGreaterEqual(len(da_ext["parameter_mutation_directives"]), 2)

    def test_read_only_seed_graph_snapshot_immutability(self) -> None:
        seed_graph = ROOT / "graph" / "nee_graph.sqlite"
        if not seed_graph.is_file():
            self.skipTest("Seed graph/nee_graph.sqlite not present")
        sha_before = sha256_file(seed_graph)
        conn = connect_sqlite(seed_graph, readonly=True)
        try:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM assertion").fetchone()
            self.assertGreater(int(row["cnt"]), 0)
        finally:
            conn.close()
        sha_after = sha256_file(seed_graph)
        self.assertEqual(sha_before, sha_after)


if __name__ == "__main__":
    unittest.main()
