#!/usr/bin/env python3
"""Tests for Wave 1: Zero-Hop Inline Canonical Promotion at Intake (U0 -> L1 -> X3 -> GraphWriter)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import seal_artifact
from dual_pipeline.core import ROOT, connect_sqlite, read_json, sha256_file, write_json
from dual_pipeline.graph_delta import build_actual_graph_delta
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.intake import IntakeEngine
from dual_pipeline.intake_core.dispositions import _passes_inline_canonical_promotion_gate
from tools.prepare_evidence_envelope_intake import prepare_package


def _build_sample_source_and_draft(tmp_path: Path, *, inline_flag: bool = False) -> tuple[Path, Path]:
    source_path = tmp_path / "01_2026-10-29_hitl_resolved_transcript.md"
    source_text = (
        "# Apex Global Analytics Sprint Sync\n\n"
        "## Executive Summary\n"
        "Alex Rivera confirmed the Q4 canonical promotion pipeline is live for the Atlas workstream. "
        "The team decided to promote grounded meeting signals directly to canonical during intake. "
        "Delayed sidecar indexing above 120 seconds is tracked as a primary operational risk. "
        "Alex Rivera will validate the sidecar delta cache before Friday. "
        + (" ".join(f"meeting_context_word_{i}" for i in range(60)))
    )
    source_path.write_text(source_text, encoding="utf-8")

    draft = {
        "meeting_id": "meeting-20261029-inline-canonical",
        "title": "Apex Global Analytics Sprint Sync",
        "date": "2026-10-29",
        "classification": "internal",
        "audiences": ["nee-engineering"],
        "source_location": "archive://meetings/2026-10-29-sprint-sync.md",
        **({"inline_canonical_promotion": True} if inline_flag else {}),
        "evidence_index": {
            "E-01": {
                "speaker": "Alex Rivera",
                "role_or_org": "Orion Sprint Workstream Lead",
                "source_classification": "decision",
                "exact_text": "The team decided to promote grounded meeting signals directly to canonical during intake.",
                "locator": {"kind": "section", "value": "## Executive Summary"},
            },
            "E-02": {
                "speaker": "Alex Rivera",
                "role_or_org": "Orion Sprint Workstream Lead",
                "source_classification": "risk",
                "exact_text": "Delayed sidecar indexing above 120 seconds is tracked as a primary operational risk.",
                "locator": {"kind": "section", "value": "## Executive Summary"},
            },
            "E-03": {
                "speaker": "Alex Rivera",
                "role_or_org": "Orion Sprint Workstream Lead",
                "source_classification": "action",
                "exact_text": "Alex Rivera will validate the sidecar delta cache before Friday.",
                "locator": {"kind": "section", "value": "## Executive Summary"},
            },
        },
        "actions": [
            {
                "action_id": "act-01",
                "action": "Validate the sidecar delta cache before Friday.",
                "reported_owner": "Alex Rivera",
                "locator": {"kind": "section", "value": "## Executive Summary"},
                "evidence_ids": ["E-03"],
            }
        ],
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:alex_rivera",
                    "name": "Alex Rivera",
                    "type": "person",
                    "description": "Atlas Workstream Lead",
                    "evidence_ids": ["E-01"],
                },
                {
                    "id": "workstream:atlas_inline",
                    "name": "Orion Sprint",
                    "type": "workstream",
                    "description": "Orion sprint workstream for inline canonical promotion",
                    "evidence_ids": ["E-01"],
                },
                {
                    "id": "system:delta_sidecar_cache",
                    "name": "Delta Sidecar Cache",
                    "type": "system",
                    "description": "Incremental BGE-M3 sidecar cache",
                    "evidence_ids": ["E-01"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-inline-01",
                    "from_id": "workstream:atlas_inline",
                    "to_id": "system:delta_sidecar_cache",
                    "predicate": "DEPENDS_ON",
                    "evidence_ids": ["E-01"],
                }
            ],
            "decision_updates": [
                {
                    "decision_id": "decision:dec-inline-01",
                    "decision_text": "Promote grounded meeting signals directly to canonical during intake.",
                    "entity_id": "workstream:atlas_inline",
                    "evidence_ids": ["E-01"],
                }
            ],
            "risk_updates": [
                {
                    "risk_id": "risk:risk-inline-01",
                    "risk_text": "Delayed sidecar indexing above 120 seconds is a primary operational risk.",
                    "entity_id": "workstream:atlas_inline",
                    "evidence_ids": ["E-02"],
                }
            ],
        },
    }
    draft_path = tmp_path / "03_2026-10-29_canonical_signal_projection.json"
    write_json(draft_path, draft)
    return source_path, draft_path


class InlineCanonicalPromotionTests(unittest.TestCase):
    def test_e2e_inline_canonical_promotion_from_authorized_release_decision(self) -> None:
        """Grounded signals from an authorized transcript synthesis folder promote directly to canonical."""
        graph_src = ROOT / "graph" / "nee_graph.sqlite"
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            tmp_path = Path(tmp)
            graph_path = tmp_path / "nee_graph.sqlite"
            graph_path.write_bytes(graph_src.read_bytes())
            runtime_root = tmp_path / "runtime"

            source_path, draft_path = _build_sample_source_and_draft(tmp_path)
            write_json(
                tmp_path / "final_release_decision.json",
                {"status": "RELEASE_AUTHORIZED", "authoritative_write_authorized": True},
            )
            envelope_path = tmp_path / "envelope.json"
            envelope = prepare_package(draft_path, source_path, envelope_path, overwrite=True)

            self.assertEqual("governed_internal", envelope["extensions"]["com.partner_advisory.nee.source-posture"])
            self.assertEqual(7, envelope["extensions"]["com.partner_advisory.nee.authority-rank"])
            self.assertTrue(envelope["extensions"]["com.partner_advisory.nee.inline-canonical-promotion"])

            engine = IntakeEngine(graph_path, runtime_root)
            summary = engine.ingest(envelope_path)
            self.assertEqual("COMPLETED", summary["status"])
            run_id = summary["run_id"]
            run_dir = runtime_root / "runs" / run_id

            change_set = read_json(run_dir / "graph_change_set.json")
            planned_delta = read_json(run_dir / "graph_delta_summary.json")
            hitl_batches = read_json(run_dir / "hitl_batches.json")
            self.assertEqual(0, len(hitl_batches["review_batches"]))

            op_types = [op["operation"] for op in change_set["operations"]]
            self.assertIn("add_approved_assertion", op_types)
            self.assertNotIn("add_candidate_assertion", op_types)

            approved_cs = dict(change_set)
            approved_cs["status"] = "approved"
            approved_cs["approval_id"] = "approval-inline-e2e-001"
            approved_cs = seal_artifact(approved_cs)

            writer = GovernedGraphWriter(graph_path)
            receipt = writer.apply(approved_cs)
            self.assertEqual("COMMITTED", receipt["status"])

            actual_delta = build_actual_graph_delta(
                planned_summaries=[planned_delta],
                before_graph_path=graph_src,
                after_graph_path=graph_path,
            )
            self.assertEqual("PASS", actual_delta["status"])
            delta_by_key = {row["component_key"]: row["actual_delta"] for row in actual_delta["table"]}
            self.assertGreaterEqual(delta_by_key["canonical_assertion"], 3)
            self.assertEqual(0, delta_by_key["candidate_assertion"])

            conn = connect_sqlite(graph_path, readonly=True)
            try:
                written_ids = [
                    op["target_id"]
                    for op in change_set["operations"]
                    if op["operation"] in {"add_approved_assertion", "add_relationship", "add_action_item"}
                ]
                placeholders = ",".join("?" for _ in written_ids)
                rows = conn.execute(
                    f"SELECT assertion_id, canonical_status, canonical_eligible, retrieval_eligible, approval_reference "
                    f"FROM assertion WHERE assertion_id IN ({placeholders})",
                    written_ids,
                ).fetchall()
                self.assertEqual(len(written_ids), len(rows))
                for r in rows:
                    self.assertEqual("canonical", r["canonical_status"])
                    self.assertEqual(1, r["canonical_eligible"])
                    self.assertEqual(1, r["retrieval_eligible"])
                    self.assertEqual("approval-inline-e2e-001", r["approval_reference"])

                sd_rows = conn.execute(
                    f"SELECT assertion_id, canonical_status, retrieval_eligible FROM search_document WHERE assertion_id IN ({placeholders})",
                    written_ids,
                ).fetchall()
                self.assertEqual(len(written_ids), len(sd_rows))
                for sr in sd_rows:
                    self.assertEqual("canonical", sr["canonical_status"])
                    self.assertEqual(1, sr["retrieval_eligible"])
            finally:
                conn.close()

    def test_adversarial_boundary_and_gate_bypass_attempts(self) -> None:
        """Categories 1 & 3: Boundary confidence thresholds and epistemic safety blocks."""
        base_kwargs = {
            "inline_promo": True,
            "route": "NEW_CANDIDATE",
            "identity_resolved": True,
            "material_contradiction_resolved": True,
            "source_only": False,
            "forced_hitl": False,
            "correction": {},
            "has_status_snapshot": False,
        }
        # Boundary 0.75 vs 0.74 for grounded factual assertion
        self.assertTrue(
            _passes_inline_canonical_promotion_gate(
                **base_kwargs,
                candidate={"candidate_type": "assertion", "assertion_type": "factual_claim", "evidence_ids": ["ev-1"], "extraction_confidence": 0.75},
            )
        )
        self.assertFalse(
            _passes_inline_canonical_promotion_gate(
                **base_kwargs,
                candidate={"candidate_type": "assertion", "assertion_type": "factual_claim", "evidence_ids": ["ev-1"], "extraction_confidence": 0.74},
            )
        )
        # Boundary 0.85 vs 0.84 for speculative assumption/hypothesis
        self.assertTrue(
            _passes_inline_canonical_promotion_gate(
                **base_kwargs,
                candidate={"candidate_type": "assertion", "assertion_type": "hypothesis", "evidence_ids": ["ev-1"], "extraction_confidence": 0.85},
            )
        )
        self.assertFalse(
            _passes_inline_canonical_promotion_gate(
                **base_kwargs,
                candidate={"candidate_type": "assertion", "assertion_type": "hypothesis", "evidence_ids": ["ev-1"], "extraction_confidence": 0.84},
            )
        )
        # Bypass attempts: open_question, source_chunk, CONTRADICTION route, forced_hitl, SOURCE_ONLY
        for bad_cand, extra in [
            ({"candidate_type": "open_question", "assertion_type": "open_question", "evidence_ids": ["ev-1"], "extraction_confidence": 0.95}, {}),
            ({"candidate_type": "source_chunk", "assertion_type": "candidate_assertion", "evidence_ids": ["ev-1"], "extraction_confidence": 0.95}, {}),
            ({"candidate_type": "assertion", "assertion_type": "factual_claim", "evidence_ids": ["ev-1"], "extraction_confidence": 0.95, "document_signal_disposition": "SOURCE_ONLY"}, {}),
            ({"candidate_type": "assertion", "assertion_type": "factual_claim", "evidence_ids": ["ev-1"], "extraction_confidence": 0.95}, {"route": "CONTRADICTION"}),
            ({"candidate_type": "assertion", "assertion_type": "factual_claim", "evidence_ids": ["ev-1"], "extraction_confidence": 0.95}, {"forced_hitl": True}),
        ]:
            merged = {**base_kwargs, **extra}
            self.assertFalse(_passes_inline_canonical_promotion_gate(**merged, candidate=bad_cand))

    def test_adversarial_malformed_release_decision_and_rollback_integrity(self) -> None:
        """Categories 2 & 4: Unauthorized release decision fails closed; writer rollback leaves DB unchanged."""
        graph_src = ROOT / "graph" / "nee_graph.sqlite"
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            tmp_path = Path(tmp)
            source_path, draft_path = _build_sample_source_and_draft(tmp_path)
            write_json(
                tmp_path / "final_release_decision.json",
                {"status": "BLOCKED", "authoritative_write_authorized": False},
            )
            with self.assertRaisesRegex(RuntimeError, "Release authorization rejected"):
                prepare_package(draft_path, source_path, tmp_path / "envelope.json", overwrite=True)

            # Remove blocked release decision and test writer rollback when inline_canonical_promotion is False and approval is missing
            (tmp_path / "final_release_decision.json").unlink()
            graph_path = tmp_path / "nee_graph.sqlite"
            graph_path.write_bytes(graph_src.read_bytes())
            before_sha = sha256_file(graph_path)

            source_path2, draft_path2 = _build_sample_source_and_draft(tmp_path, inline_flag=True)
            envelope_path = tmp_path / "envelope.json"
            prepare_package(draft_path2, source_path2, envelope_path, overwrite=True)
            engine = IntakeEngine(graph_path, tmp_path / "runtime")
            summary = engine.ingest(envelope_path)
            change_set = read_json(tmp_path / "runtime" / "runs" / summary["run_id"] / "graph_change_set.json")

            # Tamper change_set to strip inline_canonical_promotion while using an unregistered approval_id
            tampered = dict(change_set)
            tampered["status"] = "approved"
            tampered["approval_id"] = "unregistered-approval-999"
            tampered["extensions"] = dict(change_set["extensions"])
            tampered["extensions"]["com.partner_advisory.nee.source"] = dict(change_set["extensions"]["com.partner_advisory.nee.source"])
            tampered["extensions"]["com.partner_advisory.nee.source"]["inline_canonical_promotion"] = False
            tampered = seal_artifact(tampered)

            writer = GovernedGraphWriter(graph_path)
            with self.assertRaisesRegex(RuntimeError, "approved assertion requires an active graph governance approval"):
                writer.apply(tampered)
            self.assertEqual(before_sha, sha256_file(graph_path))


if __name__ == "__main__":
    unittest.main()
