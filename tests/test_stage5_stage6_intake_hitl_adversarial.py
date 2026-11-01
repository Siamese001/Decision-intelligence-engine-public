#!/usr/bin/env python3
"""Adversarial and edge-case tests for Stage 5 (U0/L1/L0/C0.2/X1-X3) and Stage 6 (HITL/Resume).

Covers:
1. Boundary payload (empty/minimal signal updates, zero decisions on required review)
2. Malformed input (tampered approval request digests, invalid schema repair outputs)
3. Concurrency & race (multi-threaded locator validation without shared dict mutation)
4. Invariant bypass attempt (directive_id scope bypass on changed delta_digest, A2A finalization binding)
5. Rollback & recovery (binding drift invalidating disk attempt fallback, context/HITL ledger drift)
6. Exact production regressions (D-05-01..08 and DEF-S6-01..14)
"""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.canonical_pipeline.document_remediation import prepare_document_patch_or_handoff
from dual_pipeline.canonical_pipeline.document_resume import (
    _only_bounded_remediation_prompt_changed,
    _try_resume_cached_payload,
)
from dual_pipeline.canonical_pipeline.document_resume_binding import (
    _judge_binding,
    _resolve_provider_revision,
)
from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import ROOT, digest, read_json, sha256_file, stable_id, utc_now, write_json
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    _evaluate_clearance_record,
    _matches_clearance_scope,
    build_active_ingestion_approval_request,
    dispatch_and_persist_approval_request,
)
from dual_pipeline.identity import IDENTITY_REVIEW_EXTENSION_KEY, NEW_NODE_ASSESSMENT_EXTENSION_KEY
from dual_pipeline.identity_core.catalog import GraphEntityCatalog
from dual_pipeline.ingestion_reasoning_core.advisory import (
    IngestionProviderRequest,
    ingestion_proposal_output_schema,
    ingestion_user_payload,
    validate_ingestion_proposal_output,
)
from dual_pipeline. ingestion_reasoning_core.execution_runner import build_call_request
from dual_pipeline.intake import IntakeEngine
from dual_pipeline.intake_core.change_set import build_graph_change_set
from dual_pipeline.intake_core.change_set_helpers import collect_document_connected_entities
from dual_pipeline.intake_core.comparison import compare_candidates
from dual_pipeline.intake_core.decision_ledger import (
    build_ingestion_decision_explanation,
    build_ingestion_decision_ledger,
)
from dual_pipeline.intake_core.dispositions import build_dispositions
from dual_pipeline.intake_core.graph_delta_distiller import (
    _load_existing_graph_state,
    distill_document_graph_delta,
)
from dual_pipeline.intake_core.hitl import build_hitl_batches
from dual_pipeline.intake_core.proposals import (
    NEW_INTERNAL_ENTITY_DIGEST_KEY,
    NEW_INTERNAL_ENTITY_KEY,
    _new_internal_entity_proposal,
)
from dual_pipeline.intake_core.routing import build_routes
from dual_pipeline.normalization_core.meeting import normalize_evidence_envelope
from tools.prepare_evidence_envelope_intake import _validate_locators, prepare_package


def _create_test_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                description TEXT DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE node_type_registry (
                node_type_id TEXT PRIMARY KEY,
                registry_state TEXT NOT NULL DEFAULT 'active',
                identity_rule TEXT NOT NULL DEFAULT 'never auto-merge on name similarity'
            );
            CREATE TABLE canonical_alias (
                normalized_alias TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE entity_redirect (
                from_node_id TEXT PRIMARY KEY,
                to_node_id TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                object_literal TEXT,
                canonical_status TEXT NOT NULL DEFAULT 'canonical',
                currentness_state TEXT NOT NULL DEFAULT 'current',
                classification TEXT NOT NULL DEFAULT 'internal',
                allowed_audiences_json TEXT NOT NULL DEFAULT '["engineering"]',
                primary_source_id TEXT,
                extraction_run_id TEXT,
                superseded_at TEXT,
                superseded_by_assertion_id TEXT
            );
            CREATE TABLE search_document (
                search_document_id TEXT PRIMARY KEY,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                canonical_status TEXT NOT NULL DEFAULT 'canonical',
                currentness_state TEXT NOT NULL DEFAULT 'current',
                retrieval_eligible INTEGER NOT NULL DEFAULT 1,
                source_id TEXT,
                assertion_id TEXT
            );
            """
        )
        for ntype in ("workstream", "program", "stakeholder", "org", "technical_anchor", "pattern", "risk"):
            conn.execute(
                "INSERT INTO node_type_registry VALUES (?, 'active', 'never auto-merge on name similarity')",
                (ntype,),
            )
        conn.execute(
            "INSERT INTO node VALUES ('node-ws-atlas', 'Atlas', 'workstream', 'Atlas workstream', 1)"
        )
        conn.execute(
            "INSERT INTO node VALUES ('node-prog-atlas', 'Atlas', 'program', 'Atlas program', 1)"
        )
        conn.execute(
            "INSERT INTO node VALUES ('node-person-alex', 'Alex Rivera', 'stakeholder', 'Lead architect', 1)"
        )
        conn.execute(
            """INSERT INTO assertion VALUES (
                'assertion-budget-1', 'node-ws-atlas', 'HAS_CLAIM_TEXT', NULL,
                'Atlas workstream Q4 budget is $1.2M approved for platform engineering.',
                'canonical', 'current', 'internal', '["engineering"]', 'source-prior-1', 'run-intake-prior', NULL, NULL
            )"""
        )
        conn.execute(
            """INSERT INTO search_document VALUES (
                'sdoc-budget-1', 'assertion', 'assertion-budget-1', 'Atlas Budget',
                'Atlas workstream Q4 budget is $1.2M approved for platform engineering.',
                'canonical', 'current', 1, 'source-prior-1', 'assertion-budget-1'
            )"""
        )
        conn.commit()
    finally:
        conn.close()


class Stage5Stage6AdversarialTests(unittest.TestCase):
    def test_d05_01_and_d05_08_lossless_signal_projection_and_concurrent_locators(self) -> None:
        """Verify D-05-01 lossless signal preservation and D-05-08 thread-safe locator validation."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "source_doc.md"
            source_text = (
                "# Architecture Review\n\n"
                "## Section 1: Decisions and Risks\n"
                "Alex Rivera confirmed that Atlas will adopt DuckDB for local analytics. "
                "Latency above 250ms remains a critical deployment risk. "
                + (" ".join(f"word{i}" for i in range(60)))
            )
            source_path.write_text(source_text, encoding="utf-8")
            shared_locator = {"kind": "attachment", "value": "source_doc.md"}

            draft = {
                "meeting_id": "meeting-20261025-arch",
                "title": "Architecture Review",
                "date": "2026-10-25",
                "classification": "internal",
                "audiences": ["engineering"],
                "source_location": "archive://docs/arch_review.md",
                "evidence_index": {
                    "E-01": {
                        "speaker": "Not Specified",
                        "source_classification": "decision",
                        "exact_text": "Alex Rivera confirmed that Atlas will adopt DuckDB for local analytics.",
                        "locator": {"kind": "section", "value": "## Section 1: Decisions and Risks"},
                    },
                    "E-02": {
                        "speaker": "Not Specified",
                        "source_classification": "summary",
                        "exact_text": "Latency above 250ms remains a critical deployment risk.",
                        "locator": {"kind": "section", "value": "## Section 1: Decisions and Risks"},
                    },
                },
                "graph_mutations": {
                    "entity_upserts": [
                        {"id": "workstream:atlas", "name": "Atlas", "type": "workstream", "evidence_ids": ["E-01"]},
                    ],
                    "relationship_signals": [
                        {
                            "signal_id": "sig-01",
                            "from_id": "workstream:atlas",
                            "to_id": "workstream:atlas",
                            "predicate": "DEPENDS_ON",
                            "evidence_ids": ["E-01"],
                        }
                    ],
                    "decision_updates": [
                        {
                            "decision_id": "decision:dec-01",
                            "decision_text": "Adopt DuckDB for local analytics in Atlas.",
                            "entity_id": "workstream:atlas",
                            "evidence_ids": ["E-01"],
                        }
                    ],
                    "proposal_updates": [
                        {
                            "proposal_id": "proposal:prop-01",
                            "proposal_text": "Evaluate arrow flight transport for cross-node queries.",
                            "entity_id": "workstream:atlas",
                            "evidence_ids": ["E-01"],
                        }
                    ],
                    "risk_updates": [
                        {
                            "risk_id": "risk:risk-01",
                            "risk_text": "Latency above 250ms remains a critical deployment risk.",
                            "entity_id": "workstream:atlas",
                            "evidence_ids": ["E-02"],
                        }
                    ],
                    "strategic_intent_signals": [
                        {
                            "signal_id": "strat-01",
                            "signal_text": "Consolidate analytical storage onto zero-copy local engines.",
                            "entity_id": "workstream:atlas",
                            "evidence_ids": ["E-01"],
                        }
                    ],
                },
            }
            draft_path = tmp_path / "draft.json"
            out_path = tmp_path / "envelope.json"
            write_json(draft_path, draft)

            envelope = prepare_package(draft_path, source_path, out_path, overwrite=True)
            proj_ext = envelope["extensions"]["com.partner_advisory.nee.projection"]
            self.assertEqual(1, len(proj_ext["decision_updates"]))
            self.assertEqual(1, len(proj_ext["proposal_updates"]))
            self.assertEqual(1, len(proj_ext["risk_updates"]))
            self.assertEqual(1, len(proj_ext["strategic_intent_signals"]))

            bundle = normalize_evidence_envelope(envelope)
            cand_types = {c["candidate_type"] for c in bundle["candidates"]}
            assert_types = {c["assertion_type"] for c in bundle["candidates"]}
            self.assertIn("decision", cand_types)
            self.assertIn("proposal", assert_types)
            self.assertIn("risk", cand_types)
            self.assertIn("assertion", cand_types)

            # Concurrency check on _validate_locators with shared dict reference
            pkg_shared = {
                "speakers": [
                    {
                        "speaker_ref": "spk-1",
                        "statements": [
                            {"statement_id": f"s-{i}", "text": "Alex Rivera confirmed", "locator": shared_locator}
                            for i in range(8)
                        ],
                    }
                ],
                "actions": [],
            }
            _validate_locators(pkg_shared, source_path)
            self.assertEqual("attachment", shared_locator["kind"])
            for stmt in pkg_shared["speakers"][0]["statements"]:
                self.assertEqual("region", stmt["locator"]["kind"])

    def test_d05_02_and_d05_05_graph_delta_distiller_sql_and_contradiction_routing(self) -> None:
        """Verify D-05-02 canonical assertion SQL compatibility and D-05-05 contradiction detection."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            _create_test_graph(db_path)

            nodes_by_id, _, assertions = _load_existing_graph_state(db_path)
            self.assertIn("node-ws-atlas", nodes_by_id)
            self.assertEqual(1, len(assertions))
            self.assertIn("$1.2M", assertions[0]["claim_text"])
            self.assertEqual("source-prior-1", assertions[0]["source_id"])

            bundle = {
                "run_id": "run-intake-test05",
                "trace_id": "0" * 32,
                "source_id": "source-new-05",
                "source_version_id": "sv-new-05",
                "candidates": [
                    {
                        "candidate_id": "cand-contradict-1",
                        "candidate_type": "assertion",
                        "assertion_type": "candidate_assertion",
                        "normalized_text": "Atlas workstream Q4 budget is $2.5M approved for platform engineering.",
                        "materiality": "high",
                        "entity_mentions": [
                            {
                                "mention_id": "m-1",
                                "reported_text": "Atlas",
                                "stable_identifier": "node-ws-atlas",
                                "context": "Atlas budget",
                            }
                        ],
                        "evidence_ids": ["ev-1"],
                        "source_locator": {"kind": "section", "value": "## Budget"},
                        "classification": "internal",
                        "audiences": ["engineering"],
                        "extraction_confidence": 0.9,
                    }
                ],
                "extensions": {},
            }
            comp = compare_candidates(bundle, db_path)
            cand_comp = comp["comparisons"]["cand-contradict-1"]
            self.assertTrue(len(cand_comp["partial_overlaps"]) >= 1)

            resolutions = [
                {
                    "mention_id": "m-1",
                    "resolution_id": "res-1",
                    "reported_name": "Atlas",
                    "stable_identifier": {"value": "node-ws-atlas"},
                    "selected_entity_id": "node-ws-atlas",
                    "outcome": "AUTO_LINK_EXACT",
                    "requires_hitl": False,
                    "alternatives": [],
                    "extensions": {},
                }
            ]
            routes = build_routes(bundle, resolutions, comp)
            self.assertEqual("CONTRADICTION", routes[0]["intake_route"])

            delta_report = distill_document_graph_delta(bundle, resolutions, comp, db_path)
            self.assertEqual(1, len(delta_report.corroborated_facts))

    def test_d05_03_and_d05_06_catalog_homonym_narrowing_and_advisory_repair(self) -> None:
        """Verify D-05-03 declared_type narrowing and D-05-06 advisory outcome & repair context."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            _create_test_graph(db_path)
            catalog = GraphEntityCatalog(db_path)

            untyped_matches, _ = catalog.exact("Atlas", None)
            self.assertEqual(2, len(untyped_matches))
            typed_matches, basis = catalog.exact("Atlas", None, declared_type="workstream")
            self.assertEqual(["node-ws-atlas"], typed_matches)
            self.assertEqual("exact_normalized_name", basis)

            packet = {
                "candidates": [
                    {
                        "candidate_id": "cand-exact-1",
                        "evidence_ids": ["ev-1"],
                        "entity_resolution_outcomes": ["AUTO_LINK_EXACT"],
                    }
                ]
            }
            valid_output = {
                "recommendations": [
                    {
                        "candidate_id": "cand-exact-1",
                        "recommendation": "CANDIDATE",
                        "evidence_ids": ["ev-1"],
                        "reason_codes": ["EXACT_IDENTITY_MATCH"],
                        "reviewer_question": None,
                    }
                ],
                "overall_review_priority": "low",
                "summary_reason_codes": ["CLEAN_PACKET"],
            }
            validated = validate_ingestion_proposal_output(valid_output, packet)
            self.assertEqual("CANDIDATE", validated["recommendations"][0]["recommendation"])

            schema = ingestion_proposal_output_schema(packet)
            primary_req = IngestionProviderRequest(purpose="PRIMARY", packet=packet, output_schema=schema)
            repair_req = build_call_request(
                primary_req, "SCHEMA_REPAIR", schema, {"recommendations": []}, ("missing item",), packet=packet
            )
            self.assertEqual(packet, repair_req.packet)
            user_payload = ingestion_user_payload(repair_req)
            self.assertIn("candidate_context", user_payload)
            self.assertEqual("cand-exact-1", user_payload["candidate_context"][0]["candidate_id"])

    def test_d05_04_and_d05_07_multi_mention_create_new_and_transcript_connected_entities(self) -> None:
        """Verify D-05-04 multi-mention CREATE_NEW & relationship mapping and D-05-07 transcript guard."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            _create_test_graph(db_path)

            prop = {
                "canonical_name": "Quantum Ledger",
                "description": "New ledger service",
                "entity_id": stable_id("entity", "technical_anchor", "src-1", "sys:quantum", length=28),
                "node_type": "technical_anchor",
                "status": "candidate",
                "source_id": "src-1",
                "source_version_id": "sv-1",
                "source_stable_identifier": "sys:quantum",
                "source_profile_digest": "a" * 64,
                "evidence_ids": ["ev-1"],
                "affected_candidate_ids": ["cand-rel-1"],
                "possible_duplicate_labels": [],
            }
            prop_digest = digest(prop)
            cand = {
                "candidate_id": "cand-rel-1",
                "candidate_type": "relationship",
                "assertion_type": "architecture_relationship",
                "normalized_text": "Atlas DEPENDS_ON Quantum Ledger.",
                "materiality": "high",
                "evidence_ids": ["ev-1"],
                "classification": "internal",
                "audiences": ["engineering"],
                "extraction_confidence": 0.9,
                "entity_mentions": [
                    {"mention_id": "m-1", "reported_text": "Atlas", "stable_identifier": "node-ws-atlas"},
                    {"mention_id": "m-2", "reported_text": "Quantum Ledger", "stable_identifier": "sys:quantum"},
                ],
            }
            resolutions = [
                {
                    "mention_id": "m-1",
                    "resolution_id": "res-1",
                    "reported_name": "Atlas",
                    "stable_identifier": {"value": "node-ws-atlas"},
                    "selected_entity_id": "node-ws-atlas",
                    "outcome": "AUTO_LINK_EXACT",
                    "requires_hitl": False,
                    "alternatives": [],
                    "extensions": {},
                },
                {
                    "mention_id": "m-2",
                    "resolution_id": "res-2",
                    "reported_name": "Quantum Ledger",
                    "stable_identifier": {"value": "sys:quantum"},
                    "selected_entity_id": None,
                    "outcome": "CREATE_NEW_ENTITY_PROPOSAL",
                    "requires_hitl": True,
                    "alternatives": [],
                    "extensions": {
                        NEW_NODE_ASSESSMENT_EXTENSION_KEY: {
                            "status": "PROPOSE_NEW_CANDIDATE",
                            "proposal": prop,
                            "proposal_digest": prop_digest,
                        }
                    },
                },
            ]
            decision = create_artifact(
                "hitl-decision",
                "hitl-decision-test01",
                decision_id="hitl-decision-test01",
                run_id="run-intake-test04",
                trace_id="0" * 32,
                review_item_id="review-item-test01",
                tier=2,
                decision="CREATE_NEW",
                reviewer="operator@consulting.example.com",
                rationale="Approve new technical anchor",
                affected_candidate_ids=["cand-rel-1"],
                alternatives=[],
                challenger_required=False,
                challenger_id=None,
                evidence_reviewed=["ev-1"],
                before_state_digest="0" * 64,
                proposed_after_state_digest=digest(cand),
                decided_at=utc_now(),
                extensions={
                    NEW_INTERNAL_ENTITY_KEY: prop,
                    NEW_INTERNAL_ENTITY_DIGEST_KEY: prop_digest,
                },
            )
            validated_prop = _new_internal_entity_proposal(
                candidate=cand, resolutions=resolutions, decision=decision
            )
            self.assertEqual(prop["entity_id"], validated_prop["entity_id"])

            bundle = {
                "run_id": "run-intake-test04",
                "trace_id": "0" * 32,
                "source_id": "src-1",
                "source_version_id": "sv-1",
                "meeting": {"meeting_id": "meeting-planning-sync", "title": "Q4 Planning Meeting"},
                "candidates": [cand],
                "evidence": [{"evidence_id": "ev-1", "locator": {"kind": "timestamp", "value": "00:01:00"}}],
                "extensions": {
                    "com.partner_advisory.nee.source": {
                        "name": "Q4 Planning Meeting",
                        "source_location": "output/q4_planning_live_transcript.md",
                        "source_type": "meeting_transcript",
                        "attendees": ["Alex Rivera"],
                    }
                },
            }
            routes = [
                {
                    "subject_id": "cand-rel-1",
                    "intake_route": "NEW_RELATIONSHIP",
                    "reason_codes": ["relationship candidate"],
                }
            ]
            dispositions = build_dispositions(bundle, routes, resolutions, [decision])
            self.assertEqual("CANDIDATE", dispositions[0]["disposition"])

            # D-05-07: transcript with "planning" in title must not return document-connected entities
            connected = collect_document_connected_entities(
                bundle, dispositions, {"cand-rel-1": cand}, {"cand-rel-1": resolutions}, {"cand-rel-1": decision}
            )
            self.assertEqual(set(), connected)

            cs = build_graph_change_set(bundle, dispositions, resolutions, db_path, [decision])
            self.assertIsNotNone(cs)
            rel_ops = [op for op in cs["operations"] if op["operation"] == "add_relationship"]
            self.assertEqual(1, len(rel_ops))
            op_payload = cs["extensions"]["com.partner_advisory.nee.operation-payloads"][rel_ops[0]["operation_id"]]
            self.assertEqual("node-ws-atlas", op_payload["subject_node_id"])
            self.assertEqual(prop["entity_id"], op_payload["object_node_id"])

    def test_def_s6_01_07_13_active_ingestion_approval_fail_closed(self) -> None:
        """Verify DEF-S6-01 delta_digest mismatch rejection, DEF-S6-07 decline wording, and DEF-S6-13 tamper detection."""
        record = {
            "decision_id": "dec-1",
            "operator_id": "amit.ayer@consulting.example.com",
            "authority_type": "HUMAN_OPERATOR",
            "outcome_type": "RESOLVED",
            "selected_option": "Decline candidate publication",
            "metadata": {
                "directive_id": "dir-100",
                "run_id": "run-100",
                "delta_digest": "digest_v1",
                "approval_origin": "interactive_hitl_cli",
            },
        }
        # DEF-S6-01: same directive_id but changed delta_digest must return False
        self.assertFalse(
            _matches_clearance_scope(record, "dir-100", "run-100", "digest_v2", {"dir-100", "run-100", "digest_v2"})
        )
        self.assertTrue(
            _matches_clearance_scope(record, "dir-100", "run-100", "digest_v1", {"dir-100", "run-100", "digest_v1"})
        )

        # DEF-S6-07: "Decline candidate publication" must evaluate to False (DECLINED), never True
        cleared, reason = _evaluate_clearance_record(record)
        self.assertFalse(cleared)
        self.assertIn("DECLINED", reason)

        # DEF-S6-13: tampered existing request file must not be blindly trusted
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            req = build_active_ingestion_approval_request(
                run_id="run-s6-13",
                directive_id="dir-s6-13",
                project="nea",
                namespace="ns",
                source_package_digest="a" * 64,
                proposed_delta_digest="b" * 64,
                baseline_generation="gen-1",
                table_deltas={"node": 1},
                runtime_root=str(tmp_root),
            )
            req_dir = tmp_root / "approval_requests"
            req_dir.mkdir(parents=True, exist_ok=True)
            req_file = req_dir / f"{req['request_id']}.json"
            tampered = dict(req)
            tampered["response_status"] = "APPROVED"
            tampered["proposed_delta_digest"] = "c" * 64  # tampered without resealing or matching request
            write_json(req_file, tampered)

            dispatched = dispatch_and_persist_approval_request(
                req,
                transport=lambda r: {"selected_option": "Decline candidate publication", "operator_id": "amit.ayer"},
                ledger_path=tmp_root / "ledger.json",
                storage_root=tmp_root,
            )
            self.assertEqual("DECLINED", dispatched["response_status"])

    def test_def_s6_02_05_08_09_document_resume_and_remediation_fail_closed(self) -> None:
        """Verify DEF-S6-02 disk fallback guard, DEF-S6-05 remediation_directive, DEF-S6-08 unbound revision, DEF-S6-09 context/ledger drift."""
        # DEF-S6-08: unbound live provider revision must return None and produce blocker
        class _UnboundProvider:
            d03_model = "gpt-6.1-sol"

        self.assertIsNone(_resolve_provider_revision(_UnboundProvider(), None, "gpt-6.1-sol"))
        _, judge_blockers = _judge_binding(
            {}, "llm", "gpt-6.1-sol", "high", "gpt-6.1-sol", "high", False, None, None
        )
        self.assertIn("JUDGE_MODEL_REVISION_UNBOUND", judge_blockers)

        # DEF-S6-09: changes to hitl_ledger_sha256 or context_sha256 must NOT be ignored
        before = {
            "schema_version": "document-resume-binding-1",
            "reuse_blockers": [],
            "components": {
                "prompts": {"STAGE_03_DOCUMENT_GRAPH": "p1", "document_user_content": "u1"},
                "hitl_ledger_sha256": "ledger_v1",
                "context_sha256": "ctx_v1",
            },
        }
        after_ledger_changed = copy.deepcopy(before)
        after_ledger_changed["components"]["hitl_ledger_sha256"] = "ledger_v2"
        self.assertFalse(_only_bounded_remediation_prompt_changed(before, after_ledger_changed))

        after_ctx_changed = copy.deepcopy(before)
        after_ctx_changed["components"]["context_sha256"] = "ctx_v2"
        self.assertFalse(_only_bounded_remediation_prompt_changed(before, after_ctx_changed))

        # DEF-S6-02: _try_resume_cached_payload must not resume from disk when binding is not reusable or BINDING_CHANGED
        with tempfile.TemporaryDirectory() as tmp:
            res = _try_resume_cached_payload(
                None,
                {"reusable": False, "reuse_blockers": ["EXTRACTION_MODEL_REVISION_UNBOUND"]},
                {"out_p": Path(tmp), "source_text": "text"},
                {},
                {"reason": "BINDING_CHANGED"},
                {},
            )
            self.assertIsNone(res)

        # DEF-S6-05: prepare_document_patch_or_handoff sets metadata["remediation_directive"]
        with tempfile.TemporaryDirectory() as tmp:
            meta: dict[str, Any] = {}
            source_slides = "## Slide 1\nAlpha content\n\n## Slide 2\nBeta uncovered content"
            candidate_json = json.dumps({"evidence_index": {}, "graph_mutations": {}})
            judge_report = {
                "output_3_evaluation": {
                    "remediation_directive": "Missing slide coverage for Slide 2; extract missing entities."
                }
            }
            prepare_document_patch_or_handoff(
                meta,
                candidate=candidate_json,
                judge_report=judge_report,
                source_text=source_slides,
                directive="Extract missing entities from Slide 2.",
                output_dir=Path(tmp),
                attempt=1,
            )
            self.assertTrue(meta.get("remediation_directive"))
            self.assertEqual(
                meta["remediation_directive"],
                meta["additive_unit_gap_remediation"]["enriched_directive"],
            )

    def test_def_s6_06_10_14_hitl_batch_grouping_empty_review_and_mixed_ledger_choices(self) -> None:
        """Verify DEF-S6-06 empty review rejection, DEF-S6-10 group member route escalation, and DEF-S6-14 mixed group choices."""
        group_assessment = {
            "group_id": "group-mixed-1",
            "status": "PROPOSE_NEW_CANDIDATE",
            "affected_candidate_ids": ["cand-m1", "cand-m2"],
            "proposal": {"entity_id": "ent-new-1", "canonical_name": "New Node"},
            "proposal_digest": "d" * 64,
        }
        bundle = {
            "run_id": "run-intake-s6",
            "trace_id": "0" * 32,
            "source_id": "src-s6",
            "source_version_id": "sv-s6",
            "evidence": [{"evidence_id": "ev-1", "locator": {"kind": "section", "value": "## S1"}}],
            "candidates": [
                {
                    "candidate_id": "cand-m1",
                    "candidate_type": "assertion",
                    "assertion_type": "candidate_assertion",
                    "normalized_text": "Claim one.",
                    "materiality": "medium",
                    "entity_mentions": [{"mention_id": "m-1", "reported_text": "New Node", "stable_identifier": "id-1"}],
                    "evidence_ids": ["ev-1"],
                    "source_locator": {"kind": "section", "value": "## S1"},
                    "classification": "internal",
                    "audiences": ["engineering"],
                    "extraction_confidence": 0.9,
                },
                {
                    "candidate_id": "cand-m2",
                    "candidate_type": "assertion",
                    "assertion_type": "candidate_assertion",
                    "normalized_text": "Claim two with contradiction.",
                    "materiality": "high",
                    "entity_mentions": [{"mention_id": "m-2", "reported_text": "New Node", "stable_identifier": "id-1"}],
                    "evidence_ids": ["ev-1"],
                    "source_locator": {"kind": "section", "value": "## S1"},
                    "classification": "internal",
                    "audiences": ["engineering"],
                    "extraction_confidence": 0.9,
                },
            ],
            "extensions": {},
        }
        routes = [
            {"subject_id": "cand-m1", "intake_route": "NEW_ASSERTION", "reason_codes": ["novel"]},
            {"subject_id": "cand-m2", "intake_route": "CONTRADICTION", "reason_codes": ["perspective_owner_shift"]},
        ]
        resolutions = [
            {
                "mention_id": "m-1",
                "resolution_id": "res-shared-1",
                "reported_name": "Atlas",
                "stable_identifier": {"value": "node-ws-atlas"},
                "selected_entity_id": "node-ws-atlas",
                "outcome": "AUTO_LINK_EXACT",
                "requires_hitl": False,
                "alternatives": [],
                "extensions": {
                    NEW_NODE_ASSESSMENT_EXTENSION_KEY: {
                        "group_id": "group-mixed-1",
                        "status": "EXISTING_EXACT_MATCH",
                        "declared_node_type": "workstream",
                        "proposal_digest": None,
                        "reasons": ["Exact match"],
                    },
                    IDENTITY_REVIEW_EXTENSION_KEY: {
                        "automated_outcome": "AUTO_LINK_EXACT",
                        "decision_basis": ["Exact match"],
                        "confidence": {"selected_score": 1.0},
                        "label_options": [],
                    },
                },
            },
            {
                "mention_id": "m-2",
                "resolution_id": "res-shared-1",
                "reported_name": "New Node",
                "stable_identifier": {"value": "id-1"},
                "selected_entity_id": None,
                "outcome": "CREATE_NEW_ENTITY_PROPOSAL",
                "requires_hitl": True,
                "alternatives": [{"entity_id": "node-ws-atlas", "canonical_name": "Atlas", "match_score": 0.72}],
                "extensions": {
                    NEW_NODE_ASSESSMENT_EXTENSION_KEY: {
                        **group_assessment,
                        "declared_node_type": "workstream",
                        "reasons": ["New candidate proposed"],
                    },
                    IDENTITY_REVIEW_EXTENSION_KEY: {
                        "automated_outcome": "CREATE_NEW_ENTITY_PROPOSAL",
                        "decision_basis": ["New candidate proposed"],
                        "confidence": {"selected_score": 0.0},
                        "label_options": [
                            {"decision": "CONFIRM", "available": True, "reason": "Available"},
                            {"decision": "CREATE_NEW", "available": True, "reason": "Available"},
                            {"decision": "HOLD", "available": True, "reason": "Available"},
                            {"decision": "REJECT", "available": True, "reason": "Available"},
                        ],
                    },
                },
            },
        ]
        hitl = build_hitl_batches(bundle, routes, resolutions)
        self.assertEqual(1, hitl["tier_2_count"])
        tier2_item = hitl["review_batches"][0]["items"][0]
        self.assertIn("contradiction", tier2_item["reason_codes"])
        self.assertIn("perspective owner shift", tier2_item["reason_codes"])

        # DEF-S6-14: mixed group (AUTO_LINK_EXACT + CREATE_NEW_ENTITY_PROPOSAL) in decision ledger
        dispositions = build_dispositions(bundle, routes, resolutions)
        graph_delta = {
            "operation_count": 0,
            "operation_counts": {},
            "graph_sha256_before": "0" * 64,
        }
        ledger = build_ingestion_decision_ledger(
            bundle=bundle,
            routes=routes,
            resolutions=resolutions,
            dispositions=dispositions,
            change_set=None,
            graph_delta=graph_delta,
        )
        english = build_ingestion_decision_explanation(
            ledger, technical_ledger_sha256="a" * 64, graph_delta=graph_delta
        )
        choices = {c["decision"]: c["available"] for c in english["decision_groups"][0]["reviewer_choices"]}
        self.assertTrue(choices["CONFIRM"])
        self.assertTrue(choices["CREATE_NEW"])
        self.assertTrue(choices["HOLD"])
        self.assertTrue(choices["REJECT"])


if __name__ == "__main__":
    unittest.main()
