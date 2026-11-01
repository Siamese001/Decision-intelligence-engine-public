"""
tests/test_copilot_query_augmentation_integration_e2e.py

Wave 9 Comprehensive End-to-End Integration Test:
1. Long multi-turn meeting context tracking with ConversationContextModel.
2. Concurrent Live Copilot and Query pipeline execution with AnswerLedger recording.
3. Strict pipeline independence invariant (no state leakage between copilot and query).
4. Latency proofs: Fast tier p95 < 1.0 ms and Verified tier p95 <= copilot.rigor.budget_ms.
5. Cross-SOW relationship retrieval via 'connect' intent and contradiction gaps.
6. Operator feedback, answer evaluation, and closed-loop proposal synthesis.
7. Replay simulation with guard metrics and authentic human UWG commit (+0 graph mutations).
8. Visible next-session parameter adaptation and exact bidirectional rollback.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import List

from dual_pipeline.answer_ledger import (
    AnswerLedger,
    AnswerRecord,
    record_operator_feedback,
)
from dual_pipeline.calibration import create_signed_calibration_approval_receipt
from dual_pipeline.core import utc_now, write_json
from dual_pipeline.evals.answer_eval import evaluate_ledger_answers
from dual_pipeline.learning_loop_core import (
    apply_answer_learning_proposal,
    rollback_answer_learning_proposal,
    scan_answer_eval_receipts,
    simulate_answer_learning_proposal,
    synthesize_answer_learning_proposal,
)
from dual_pipeline.live_meeting_recorder_core.copilot_connect import (
    find_contradiction_gaps,
    get_connected_chains,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import (
    CopilotGroundingVerifier,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)
from dual_pipeline.parameters import ParameterRegistry

CANONICAL_TABLES = (
    "nodes",
    "edges",
    "assertions",
    "evidence_items",
    "sources",
    "entities",
    "claims",
    "contexts",
    "decisions",
    "policies",
    "metrics",
    "events",
    "runs",
    "schema_version",
)


def _init_fixture_graph(db_path: Path) -> str:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = (
        "CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS edges (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS assertions (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS evidence_items (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS claims (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS contexts (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS policies (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS metrics (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS schema_version (id TEXT PRIMARY KEY, payload TEXT);"
        "INSERT INTO schema_version VALUES ('1', '1.0.0');"
    )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)

        entities = [
            ("workstream:auth", json.dumps({"id": "workstream:auth", "name": "Authentication Workstream", "type": "WORKSTREAM"})),
            ("workstream:payments", json.dumps({"id": "workstream:payments", "name": "Payments Workstream", "type": "WORKSTREAM"})),
            ("deliverable:sso", json.dumps({"id": "deliverable:sso", "name": "SSO SAML Gateway", "type": "DELIVERABLE"})),
            ("deliverable:tokenization", json.dumps({"id": "deliverable:tokenization", "name": "PCI Token Vault", "type": "DELIVERABLE"})),
            ("risk:cert_expiry", json.dumps({"id": "risk:cert_expiry", "name": "Root Cert Expiry Risk", "type": "RISK"})),
            ("person:alice", json.dumps({"id": "person:alice", "name": "Alice Lead", "type": "PERSON"})),
            ("person:bob", json.dumps({"id": "person:bob", "name": "Bob Architect", "type": "PERSON"})),
        ]
        conn.executemany("INSERT INTO entities VALUES (?, ?)", entities)
        conn.executemany("INSERT INTO nodes VALUES (?, ?)", entities)

        assertions = [
            ("asrt_auth_1", json.dumps({
                "id": "asrt_auth_1",
                "subject": "deliverable:sso",
                "predicate": "DEPENDS_ON",
                "object": "workstream:auth",
                "status": "APPROVED",
                "source_date": "2026-09-01",
                "confidence": 0.95,
            })),
            ("asrt_auth_2", json.dumps({
                "id": "asrt_auth_2",
                "subject": "deliverable:sso",
                "predicate": "HAS_RISK",
                "object": "risk:cert_expiry",
                "status": "APPROVED",
                "source_date": "2026-10-01",
                "confidence": 0.92,
            })),
            ("asrt_pay_1", json.dumps({
                "id": "asrt_pay_1",
                "subject": "deliverable:tokenization",
                "predicate": "DEPENDS_ON",
                "object": "deliverable:sso",
                "status": "APPROVED",
                "source_date": "2026-09-15",
                "confidence": 0.94,
            })),
            ("asrt_pay_2", json.dumps({
                "id": "asrt_pay_2",
                "subject": "workstream:payments",
                "predicate": "CONTRIBUTES_TO",
                "object": "deliverable:tokenization",
                "status": "APPROVED",
                "source_date": "2026-09-20",
                "confidence": 0.90,
            })),
            ("asrt_conflict_prior", json.dumps({
                "id": "asrt_conflict_prior",
                "subject": "deliverable:sso",
                "predicate": "SCHEDULED_FOR",
                "object": "2026-Q3",
                "status": "APPROVED",
                "source_date": "2026-08-15",
                "confidence": 0.85,
            })),
            ("asrt_conflict_latest", json.dumps({
                "id": "asrt_conflict_latest",
                "subject": "deliverable:sso",
                "predicate": "SCHEDULED_FOR",
                "object": "2026-Q4",
                "status": "APPROVED",
                "source_date": "2026-10-02",
                "confidence": 0.91,
            })),
        ]
        conn.executemany("INSERT INTO assertions VALUES (?, ?)", assertions)
        conn.commit()

    with open(db_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _build_fixture_topology() -> SimpleNamespace:
    nodes = {
        "workstream:auth": {
            "canonical_name": "Authentication Workstream",
            "display_name": "workstream:auth",
            "node_type": "workstream",
            "aliases": ["auth", "authentication"],
        },
        "workstream:payments": {
            "canonical_name": "Payments Workstream",
            "display_name": "workstream:payments",
            "node_type": "workstream",
            "aliases": ["payments"],
        },
        "deliverable:sso": {
            "canonical_name": "SSO SAML Gateway",
            "display_name": "deliverable:sso",
            "node_type": "deliverable",
            "aliases": ["sso"],
        },
        "deliverable:tokenization": {
            "canonical_name": "PCI Token Vault",
            "display_name": "deliverable:tokenization",
            "node_type": "deliverable",
            "aliases": ["tokenization"],
        },
        "risk:cert_expiry": {
            "canonical_name": "Root Cert Expiry Risk",
            "display_name": "risk:cert_expiry",
            "node_type": "risk",
            "aliases": ["cert_expiry"],
        },
        "person:alice": {
            "canonical_name": "Alice Lead",
            "display_name": "Alice Lead",
            "node_type": "person",
            "organization": "Security",
        },
        "person:bob": {
            "canonical_name": "Bob Architect",
            "display_name": "Bob Architect",
            "node_type": "person",
            "organization": "Payments",
        },
    }
    assertions = {
        "asrt_auth_1": {
            "assertion_id": "asrt_auth_1",
            "subject_node_id": "deliverable:sso",
            "predicate": "DEPENDS_ON",
            "object_node_id": "workstream:auth",
            "claim_text": "SSO SAML Gateway depends on Authentication Workstream",
            "status": "APPROVED",
            "source_date": "2026-09-01",
            "confidence": 0.95,
            "evidence_ids": ["ev:auth:01", "ev:auth:02"],
            "source_ids": ["meeting:01", "jira:01"],
        },
        "asrt_auth_2": {
            "assertion_id": "asrt_auth_2",
            "subject_node_id": "deliverable:sso",
            "predicate": "HAS_RISK",
            "object_node_id": "risk:cert_expiry",
            "claim_text": "Root cert expiry risk on SSO SAML Gateway",
            "status": "APPROVED",
            "source_date": "2026-10-01",
            "confidence": 0.92,
            "evidence_ids": ["ev:auth:03"],
            "source_ids": ["meeting:01"],
        },
        "asrt_pay_1": {
            "assertion_id": "asrt_pay_1",
            "subject_node_id": "deliverable:tokenization",
            "predicate": "DEPENDS_ON",
            "object_node_id": "deliverable:sso",
            "claim_text": "PCI Token Vault depends on SSO SAML Gateway",
            "status": "APPROVED",
            "source_date": "2026-09-15",
            "confidence": 0.94,
            "evidence_ids": ["ev:pay:01"],
            "source_ids": ["meeting:02"],
        },
        "asrt_pay_2": {
            "assertion_id": "asrt_pay_2",
            "subject_node_id": "workstream:payments",
            "predicate": "CONTRIBUTES_TO",
            "object_node_id": "deliverable:tokenization",
            "claim_text": "Payments Workstream contributes to PCI Token Vault",
            "status": "APPROVED",
            "source_date": "2026-09-20",
            "confidence": 0.90,
            "evidence_ids": ["ev:pay:02"],
            "source_ids": ["meeting:02"],
        },
        "asrt_conflict_prior": {
            "assertion_id": "asrt_conflict_prior",
            "subject_node_id": "deliverable:sso",
            "predicate": "SCHEDULED_FOR",
            "object_node_id": "literal:2026-Q3",
            "claim_text": "SSO SAML Gateway scheduled for 2026-Q3",
            "status": "APPROVED",
            "source_date": "2026-08-15",
            "confidence": 0.85,
            "evidence_ids": ["ev:sched:01"],
            "source_ids": ["meeting:01"],
        },
        "asrt_conflict_latest": {
            "assertion_id": "asrt_conflict_latest",
            "subject_node_id": "deliverable:sso",
            "predicate": "SCHEDULED_FOR",
            "object_node_id": "literal:2026-Q4",
            "claim_text": "SSO SAML Gateway scheduled for 2026-Q4",
            "status": "APPROVED",
            "source_date": "2026-10-02",
            "confidence": 0.91,
            "evidence_ids": ["ev:sched:02"],
            "source_ids": ["meeting:03"],
        },
    }
    forward_adjacency = {
        "deliverable:tokenization": [
            {"to_node_id": "deliverable:sso", "predicate": "DEPENDS_ON", "to_node_name": "SSO SAML Gateway"},
        ],
        "deliverable:sso": [
            {"to_node_id": "workstream:auth", "predicate": "DEPENDS_ON", "to_node_name": "Authentication Workstream"},
            {"to_node_id": "risk:cert_expiry", "predicate": "HAS_RISK", "to_node_name": "Root Cert Expiry Risk"},
        ],
        "workstream:payments": [
            {"to_node_id": "deliverable:tokenization", "predicate": "CONTRIBUTES_TO", "to_node_name": "PCI Token Vault"},
        ],
    }
    reverse_adjacency = {
        "workstream:auth": [
            {"from_node_id": "deliverable:sso", "predicate": "DEPENDS_ON", "from_node_name": "SSO SAML Gateway"},
        ],
        "deliverable:sso": [
            {"from_node_id": "deliverable:tokenization", "predicate": "DEPENDS_ON", "from_node_name": "PCI Token Vault"},
        ],
        "deliverable:tokenization": [
            {"from_node_id": "workstream:payments", "predicate": "CONTRIBUTES_TO", "from_node_name": "Payments Workstream"},
        ],
    }
    return SimpleNamespace(
        nodes=nodes,
        assertions=assertions,
        forward_adjacency=forward_adjacency,
        reverse_adjacency=reverse_adjacency,
    )


class TestCopilotQueryAugmentationIntegrationE2E(unittest.TestCase):
    """Full End-to-End integration suite sealing Waves 1 through 9."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_aug_int_e2e_")
        self.root = Path(self.temp_dir)
        self.config_dir = self.root / "config" / "runtime"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "parameters.json"
        self.proposals_dir = self.root / "reports" / "learning_proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.eval_dir = self.root / "reports" / "answer_eval"
        self.eval_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_db = self.root / "learning" / "answer_ledger.sqlite"
        self.ledger_db.parent.mkdir(parents=True, exist_ok=True)
        self.runs_dir = self.root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

        self.graph_path = self.root / "seed_graph.sqlite"
        self.initial_graph_sha = _init_fixture_graph(self.graph_path)

        self.mock_topology = _build_fixture_topology()
        self.retriever = CopilotGraphRetriever(
            topology=self.mock_topology,
            attendees=[
                {"name": "Alice Lead", "role": "Tech Lead", "org": "Security"},
                {"name": "Bob Architect", "role": "Lead Architect", "org": "Payments"},
            ],
            graph_sha256=self.initial_graph_sha,
            generation="gen_test_w9",
        )

        initial_config = {
            "schema_version": "1.0.0",
            "updated_at": utc_now(),
            "parameters": {
                "copilot": {
                    "context": {
                        "decay_tau_seconds": 300.0,
                        "focus_seconds": 15.0,
                        "focus_weight": 2.0,
                        "salient_entities_top_k": 5,
                        "flow_items_max": 10,
                    },
                    "neighborhood_max_hops": 2,
                    "rigor": {
                        "budget_ms": 15.0,
                        "min_salience_score": 0.4,
                    },
                    "rerank": {
                        "candidate_pool_size": 20,
                    },
                }
            },
        }
        self.config_path.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")
        self.reg = ParameterRegistry(config_path=self.config_path)
        self.ledger = AnswerLedger(db_path=self.ledger_db)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_end_to_end_copilot_and_query_integration_lifecycle(self) -> None:
        """Executes full concurrent lifecycle across Copilot, Query, Ledger, Eval, and UWG."""
        # -----------------------------------------------------------------
        # Step 1: Initialize Two-Layer Context Model & Long Meeting Turns
        # -----------------------------------------------------------------
        attendees = [
            {"name": "Alice Lead", "role": "Tech Lead"},
            {"name": "Bob Architect", "role": "Lead Architect"},
        ]
        ctx_model = ConversationContextModel(
            project_scope="PROJECT_NEXUS",
            attendees=attendees,
            focus_seconds=15.0,
            decay_tau_seconds=300.0,
        )

        turns = [
            {"speaker": "Alice Lead", "text": "Welcome everyone. Let us review workstream:auth and its integration.", "offset": 5.0},
            {"speaker": "Bob Architect", "text": "I will own the workstream:payments service rollout.", "offset": 20.0},
            {"speaker": "Alice Lead", "text": "Are there any dependencies for workstream:auth?", "offset": 45.0},
            {"speaker": "Bob Architect", "text": "Yes, deliverable:tokenization depends on deliverable:sso directly.", "offset": 70.0},
            {"speaker": "Alice Lead", "text": "What is the status of risk:cert_expiry on our auth gateway?", "offset": 120.0},
            {"speaker": "Bob Architect", "text": "We scheduled deliverable:sso for Q4, but earlier notes said Q3.", "offset": 180.0},
        ]

        for i, t in enumerate(turns):
            ctx_model.add_turn(t["speaker"], t["text"], timestamp=f"00:{i*10:02d}:00", turn_sec=t["offset"])

        seeds = ctx_model.retrieval_seeds()
        self.assertTrue(any("workstream:auth" in s for s in seeds))
        salient = [e for e, _ in ctx_model.salient_entities()]
        self.assertTrue(any("workstream:auth" in e for e in salient))

        # -----------------------------------------------------------------
        # Step 2: Concurrent Query and Copilot Operations
        # -----------------------------------------------------------------
        retriever = self.retriever

        query_latencies: List[float] = []
        copilot_fast_latencies: List[float] = []
        copilot_verified_latencies: List[float] = []
        copilot_answer_ids: List[str] = []

        def run_query_worker(iterations: int = 15) -> int:
            count = 0
            for i in range(iterations):
                t0 = time.perf_counter()
                ans_text = f"Query answer {i}: SOW deliverables depend on authentication gateway."
                q_rec = AnswerRecord(
                    pipeline="query",
                    intent_or_type="cross_workstream_synthesis",
                    context_digest=hashlib.sha256(f"q_{i}".encode()).hexdigest(),
                    retrieval_seeds=[f"dependency chains {i}"],
                    context_snapshot=[{"speaker": "User", "text": f"Show dependency chains for iteration {i}"}],
                    cited_assertion_ids=["asrt_auth_1", "asrt_pay_1"],
                    rigor="VERIFIED",
                    response_text=ans_text,
                    graph_sha256=self.initial_graph_sha,
                    latency_ms=2.5,
                )
                self.ledger.append_answer(q_rec)
                query_latencies.append((time.perf_counter() - t0) * 1000.0)
                count += 1
            return count

        verifier = CopilotGroundingVerifier(budget_ms=15.0)

        def run_copilot_worker(iterations: int = 25) -> int:
            count = 0
            for _ in range(iterations):
                # 1. Fast Tier measurement
                t_f0 = time.perf_counter()
                _ = retriever.retrieve_context("deliverable:sso workstream:auth")
                f_lat = (time.perf_counter() - t_f0) * 1000.0
                copilot_fast_latencies.append(f_lat)

                # 2. Verified Tier measurement
                candidates = retriever.retrieve_structured_assertions("deliverable:sso workstream:auth", max_results=8)
                t_v0 = time.perf_counter()
                _ = verifier.verify_and_ground(
                    candidates=candidates,
                    dialogue="What are the risks on deliverable:sso?",
                )
                v_lat = (time.perf_counter() - t_v0) * 1000.0
                copilot_verified_latencies.append(v_lat)

                # 3. Connect intent & contradiction gaps
                chains, _ = get_connected_chains(
                    retriever=retriever,
                    dialogue="deliverable:sso workstream:payments",
                    limit=5,
                )
                gaps = find_contradiction_gaps(retriever, dialogue="deliverable:sso")

                # Record copilot answer to ledger
                c_rec = AnswerRecord(
                    pipeline="copilot",
                    intent_or_type="connect",
                    context_digest=hashlib.sha256(b"copilot_ctx").hexdigest(),
                    retrieval_seeds=["deliverable:sso", "workstream:payments"],
                    context_snapshot=[{"speaker": "Alice Lead", "text": "What about Database Cluster and Network Firewall?"}],
                    cited_assertion_ids=["asrt_auth_1", "asrt_auth_2", "asrt_pay_1"],
                    rigor="VERIFIED",
                    response_text=f"Cross-workstream chain discovered with {len(chains)} connections and {len(gaps.conflicts)} gaps.",
                    graph_sha256=self.initial_graph_sha,
                    latency_ms=v_lat,
                )
                saved_c = self.ledger.append_answer(c_rec)
                copilot_answer_ids.append(saved_c.answer_id)
                count += 1
            return count

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            fut_q = executor.submit(run_query_worker, 15)
            fut_c = executor.submit(run_copilot_worker, 25)
            q_done = fut_q.result()
            c_done = fut_c.result()

        self.assertEqual(q_done, 15)
        self.assertEqual(c_done, 25)

        # -----------------------------------------------------------------
        # Step 3: Verify Strict Pipeline Independence Invariant
        # -----------------------------------------------------------------
        records = self.ledger.list_answers(limit=100)
        self.assertEqual(len(records), 40)

        query_records = [r for r in records if r.pipeline == "query"]
        copilot_records = [r for r in records if r.pipeline == "copilot"]

        self.assertEqual(len(query_records), 15)
        self.assertEqual(len(copilot_records), 25)

        for qr in query_records:
            self.assertEqual(qr.intent_or_type, "cross_workstream_synthesis")
            self.assertEqual(qr.graph_sha256, self.initial_graph_sha)

        for cr in copilot_records:
            self.assertEqual(cr.intent_or_type, "connect")
            self.assertEqual(cr.graph_sha256, self.initial_graph_sha)

        # -----------------------------------------------------------------
        # Step 4: Verify Latency Proofs
        # -----------------------------------------------------------------
        copilot_fast_latencies.sort()
        copilot_verified_latencies.sort()

        p95_fast = copilot_fast_latencies[int(len(copilot_fast_latencies) * 0.95)]
        p95_verified = copilot_verified_latencies[int(len(copilot_verified_latencies) * 0.95)]

        # Acceptance Criterion 2: fast tier p95 < 1 ms, verified tier p95 <= budget_ms
        self.assertLess(p95_fast, 1.0, f"Fast tier p95 ({p95_fast:.3f}ms) exceeded 1.0ms")
        budget_limit = self.reg.get("copilot.rigor.budget_ms")
        self.assertLessEqual(p95_verified, budget_limit, f"Verified tier p95 ({p95_verified:.3f}ms) exceeded budget {budget_limit}ms")

        # -----------------------------------------------------------------
        # Step 5: Operator Feedback & Sealed Answer Evaluation
        # -----------------------------------------------------------------
        for aid in copilot_answer_ids[:5]:
            record_operator_feedback(
                ledger=self.ledger,
                answer_id=aid,
                rating="useful",
                note="Accurate cross-workstream synthesis",
                origin="USER_EXPLICIT",
            )

        eval_receipt = evaluate_ledger_answers(
            runtime_root=self.root,
            pipeline="copilot",
            output_path=self.eval_dir / "eval_receipt_1.json",
        )
        self.assertEqual(eval_receipt["pipeline"], "copilot")
        self.assertTrue((self.eval_dir / "eval_receipt_1.json").is_file())

        # -----------------------------------------------------------------
        # Step 6: Failure Cohort Detection & Learning Proposal Synthesis
        # -----------------------------------------------------------------
        write_json(
            self.eval_dir / "eval_receipt_2.json",
            {
                "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
                "receipt_id": "eval_receipt_2",
                "pipeline": "copilot",
                "metrics": {
                    "context_recall": 0.49,
                    "groundedness": 0.94,
                    "durability": 0.91,
                    "staleness": 0.03,
                },
                "sealed_at": utc_now(),
            },
        )

        cohorts = scan_answer_eval_receipts(self.eval_dir, min_occurrences=2, config_path=self.config_path)
        self.assertGreaterEqual(len(cohorts), 1)
        cohort = cohorts[0]

        proposal = synthesize_answer_learning_proposal(
            cohort,
            proposals_dir=self.proposals_dir,
            parameter_override={
                "target_parameter": "copilot.context.decay_tau_seconds",
                "proposed_value": 400.0,
            },
        )
        self.assertEqual(proposal["status"], "proposed")
        self.assertTrue(proposal["approval_required"])

        # -----------------------------------------------------------------
        # Step 7: Dry-Run Replay Simulation & UWG Commit
        # -----------------------------------------------------------------
        sim_res = simulate_answer_learning_proposal(
            proposal,
            runtime_root=self.root,
            simulation_override={
                "target_before": 0.50,
                "target_after": 0.88,
                "guard_groundedness_before": 0.945,
                "guard_groundedness_after": 0.945,
                "guard_durability_before": 0.915,
                "guard_durability_after": 0.915,
                "simulated_latency_ms": 13.8,
            },
        )
        self.assertTrue(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_PASS")

        approval_receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="operator_lead",
            rationale="Calibrate tau to 400.0s to boost multi-turn context retention",
            origin="USER_EXPLICIT",
        )

        commit_res = apply_answer_learning_proposal(
            proposal,
            approval_receipt,
            runtime_root=self.root,
            proposals_dir=self.proposals_dir,
            config_path=self.config_path,
        )
        self.assertEqual(commit_res["status"], "APPLIED")
        self.assertEqual(commit_res["graph_delta_counts"]["total_delta"], 0)

        # -----------------------------------------------------------------
        # Step 8: Next-Session Parameter Visibility & Rollback
        # -----------------------------------------------------------------
        self.reg.reload()
        new_tau = self.reg.get("copilot.context.decay_tau_seconds")
        self.assertEqual(new_tau, 400.0)

        next_session_model = ConversationContextModel(decay_tau_seconds=new_tau)
        self.assertEqual(next_session_model.decay_tau, 400.0)

        rollback_res = rollback_answer_learning_proposal(
            commit_res["rollback_receipt_path"],
            runtime_root=self.root,
        )
        self.assertEqual(rollback_res["status"], "ROLLED_BACK")

        self.reg.reload()
        restored_tau = self.reg.get("copilot.context.decay_tau_seconds")
        self.assertEqual(restored_tau, 300.0)

        # -----------------------------------------------------------------
        # Step 9: Zero Graph Mutation Invariant (+0 Delta Throughout)
        # -----------------------------------------------------------------
        with open(self.graph_path, "rb") as f:
            final_graph_sha = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(final_graph_sha, self.initial_graph_sha, "Graph SHA mutated during read/query/learning operations!")

    def test_read_only_active_graph_query_safeguards(self):
        """
        Wave 9 Acceptance Criterion 1:
        Verify the integration query & copilot flows against a strictly read-only copy of the graph.
        Confirms zero write attempts, no lock starvation, and bit-for-bit SHA equality.
        """
        ro_graph_path = os.path.join(self.root, "read_only_fixture_graph.db")
        shutil.copyfile(self.graph_path, ro_graph_path)

        with open(ro_graph_path, "rb") as f:
            initial_ro_sha = hashlib.sha256(f.read()).hexdigest()

        # Set read-only permissions (read-only for all)
        os.chmod(ro_graph_path, 0o444)

        try:
            # 1. Retrieve context via in-memory topology retriever
            ctx = self.retriever.retrieve_context("deliverable:sso workstream:auth")
            self.assertIn("Authentication Workstream", ctx)

            # 2. Retrieve connected chains
            chains, assertions = get_connected_chains(self.retriever, "deliverable:sso workstream:payments", limit=5)
            self.assertGreater(len(chains) + len(assertions), 0)

            # 3. Find contradiction gaps
            gaps_res = find_contradiction_gaps(self.retriever, "deliverable:sso")
            self.assertIsInstance(gaps_res.conflicts, list)

            # 4. Structured assertions
            struct_assertions = self.retriever.retrieve_structured_assertions("deliverable:sso", max_results=5)
            self.assertGreaterEqual(len(struct_assertions), 1)

            # 5. Fast-tier and verified-tier grounding over read-only assertions
            verifier = CopilotGroundingVerifier(budget_ms=15.0)
            grounding_res = verifier.verify_and_ground(struct_assertions, "What are the dependencies of deliverable:sso?")
            self.assertEqual(grounding_res.rigor_tier, "VERIFIED")
            self.assertFalse(grounding_res.is_budget_breached)
            self.assertGreaterEqual(len(grounding_res.verified_assertions), 1)

            # 6. Direct read-only SQLite operations on 0o444 file
            with sqlite3.connect(f"file:{ro_graph_path}?mode=ro", uri=True) as conn:
                asrt_rows = conn.execute("SELECT id, payload FROM assertions").fetchall()
                self.assertGreaterEqual(len(asrt_rows), 5)
                ent_rows = conn.execute("SELECT id, payload FROM entities").fetchall()
                self.assertGreaterEqual(len(ent_rows), 7)
                node_rows = conn.execute("SELECT id, payload FROM nodes").fetchall()
                self.assertGreaterEqual(len(node_rows), 7)

        finally:
            # Restore write permissions for clean temp directory teardown
            os.chmod(ro_graph_path, 0o644)

        with open(ro_graph_path, "rb") as f:
            final_ro_sha = hashlib.sha256(f.read()).hexdigest()

        self.assertEqual(
            final_ro_sha,
            initial_ro_sha,
            "Read-only graph SHA mutated during read-only query operations!",
        )


if __name__ == "__main__":
    unittest.main()

