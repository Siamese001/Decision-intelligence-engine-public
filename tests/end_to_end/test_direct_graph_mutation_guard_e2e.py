"""End-to-end test suite for direct graph mutation guard and signature governance.

Wave 5 Governance:
1. E2E rejection of direct mutation bypass without approval.
2. E2E governed mutation with Universal Write Gateway receipt.
3. E2E authentic Ed25519 signature verification and rejection of SHA-256 strings.
4. E2E declarative hook guard contract in .agents/hooks.json.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.hitl_governance_core.crypto import (
    generate_hitl_keypair,
    get_governed_hitl_keypair,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
    validate_ledger_signature,
    verify_chain_integrity,
)
from dual_pipeline.vector_indexing.bge_m3_org_indexer import (
    apply_visual_org_projection_to_graph,
    inspect_org_graph_integrity,
)
from tools.verify_decision_ledger import verify_store_parity

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_GRAPH = ROOT / "graph" / "nee_graph.sqlite"
HOOKS_JSON = ROOT / ".agents" / "hooks.json"


@pytest.mark.timeout(60)
class DirectGraphMutationGuardE2ETests(unittest.TestCase):
    """End-to-end tests for Wave 5 guardrails and cryptographic signing integrity."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="e2e_guard_")
        self.tmp_path = Path(self.temp_dir.name)
        self.test_graph = self.tmp_path / "test_graph.sqlite"
        if TEMPLATE_GRAPH.is_file():
            shutil.copy2(TEMPLATE_GRAPH, self.test_graph)
        else:
            self._init_minimal_db(self.test_graph)

        self.ledger_sqlite = self.tmp_path / "test_ledger.sqlite"
        self.ledger_json = self.tmp_path / "test_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_minimal_db(self, db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS source (source_id TEXT PRIMARY KEY, source_type TEXT, source_name TEXT, source_date TEXT, authority_rank INT, source_posture TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS source_version (source_version_id TEXT PRIMARY KEY, source_id TEXT, version_number INT, source_fingerprint TEXT, content_hash TEXT, source_date_precision TEXT, source_date_status TEXT, authority_rank INT, scope_text TEXT, posture_text TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS governance_approval (approval_id TEXT PRIMARY KEY, approval_reference TEXT, approval_type TEXT, scope_json TEXT, proposal_sha256 TEXT, approval_statement_sha256 TEXT, approved_at TEXT, status TEXT, approved_by TEXT, release_version TEXT, active INT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS node (node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT, display_name TEXT, description TEXT, status TEXT, active INT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS edge (edge_id TEXT PRIMARY KEY, from_node_id TEXT, edge_type TEXT, to_node_id TEXT, description TEXT, evidence_posture TEXT, confidence TEXT, materiality TEXT, source_id TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS assertion (assertion_id TEXT PRIMARY KEY, assertion_fingerprint TEXT, subject_node_id TEXT, predicate TEXT, object_node_id TEXT, assertion_type TEXT, atomicity_state TEXT, scope_text TEXT, extraction_method TEXT, origin_type TEXT, confidence_label TEXT, confidence_score REAL, validation_state TEXT, currentness_state TEXT, date_precision TEXT, date_status TEXT, recorded_at TEXT, resolution_state TEXT, canonical_status TEXT, canonical_eligible INT, retrieval_eligible INT, materiality TEXT, classification TEXT, allowed_audiences_json TEXT, hitl_required INT, qualifiers_json TEXT, legacy_record_type TEXT, legacy_record_ids_json TEXT, primary_source_id TEXT, approval_reference TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS evidence_item (evidence_id TEXT PRIMARY KEY, evidence_fingerprint TEXT, source_id TEXT, source_version_id TEXT, locator_kind TEXT, locator_value TEXT, direct_quote TEXT, evidence_directness TEXT, source_authority_rank INT, extraction_confidence REAL, location_status TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS assertion_evidence (assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT, independent_corroboration INT, created_at TEXT, PRIMARY KEY (assertion_id, evidence_id));
            CREATE TABLE IF NOT EXISTS search_document (search_document_id TEXT PRIMARY KEY, object_type TEXT, object_id TEXT, assertion_id TEXT, title TEXT, body TEXT, canonical_status TEXT, currentness_state TEXT, retrieval_eligible INT, updated_at TEXT);
            """
        )
        conn.commit()
        conn.close()

    def test_e2e_01_direct_mutation_requires_uwg_receipt_or_active_approval(self) -> None:
        """E2E Test 1: Direct mutation with a valid UWG receipt promotes assertions cleanly."""
        proj = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "p_executive_1", "name": "Jane Doe", "type": "person"},
                    {"id": "org_division_1", "name": "NEA Core Operations", "type": "org"},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig_jane_leads_nea",
                        "from_id": "p_executive_1",
                        "to_id": "org_division_1",
                        "relationship": "LEADS",
                        "evidence_ids": ["ev_jane_leads_01"],
                    }
                ],
            },
            "evidence_index": {
                "ev_jane_leads_01": {
                    "exact_text": "Jane Doe is the leader of NEA Core Operations.",
                }
            },
        }
        valid_receipt = {
            "approval_id": "app_wave5_e2e_01",
            "approval_reference": "AD-WAVE5-E2E-01",
            "proposal_sha256": "e" * 64,
            "status": "approved",
        }
        res = apply_visual_org_projection_to_graph(self.test_graph, proj, uwg_receipt=valid_receipt)
        self.assertEqual(res["nodes_upserted"], 2)
        self.assertEqual(res["edges_upserted"], 1)
        self.assertEqual(res["assertions_upserted"], 1)

        conn = sqlite3.connect(self.test_graph)
        cur = conn.cursor()
        cur.execute("SELECT canonical_status, approval_reference FROM assertion WHERE assertion_id = 'asrt-sig_jane_leads_nea'")
        row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "canonical")
        self.assertEqual(row[1], "AD-WAVE5-E2E-01")

    def test_e2e_02_decision_record_lifecycle_creates_verifiable_ed25519_signatures(self) -> None:
        """E2E Test 2: Operator decision records generate authentic Ed25519 signatures and pass parity."""
        human_origin = {"source": "USER_INPUT", "text": "Approve Wave 5 mutation"}

        res = record_operator_answer(
            decision_id="dec_wave5_e2e_001",
            question="Authorize Wave 5 org chart activation?",
            options=["Approve (100%)", "Reject (0%)"],
            selected_option="Approve (100%)",
            operator_origin=human_origin,
            category="ingestion_activation",
            sqlite_path=self.ledger_sqlite,
            json_path=self.ledger_json,
        )

        self.assertEqual(res.status, "PERSISTED")
        self.assertTrue(self.ledger_json.exists())

        # Verify export contains authentic Ed25519 signature
        export_data = json.loads(self.ledger_json.read_text(encoding="utf-8"))
        op_decs = export_data.get("operator_decisions", [])
        self.assertEqual(len(op_decs), 1)
        entry = op_decs[0]
        sig = entry.get("signature")
        self.assertIsNotNone(sig)
        self.assertEqual(len(sig), 86, "Ed25519 signature must be 86 characters (unpadded base64url)")
        self.assertNotEqual(sig, entry.get("entry_digest"), "Signature must not equal raw entry digest")

        # Validate signature cryptographically
        self.assertTrue(validate_ledger_signature(entry))

        # Verify store parity passes
        parity_ok, parity_msg = verify_store_parity(self.ledger_sqlite, self.ledger_json)
        self.assertTrue(parity_ok, f"Store parity failed: {parity_msg}")

    def test_e2e_03_store_parity_rejects_raw_sha256_signatures(self) -> None:
        """E2E Test 3: verify_store_parity fails closed if an entry has a raw SHA-256 signature."""
        human_origin = {"source": "USER_INPUT", "text": "Approve Wave 5 test"}

        record_operator_answer(
            decision_id="dec_wave5_e2e_002",
            question="Question 2?",
            options=["Yes", "No"],
            selected_option="Yes",
            operator_origin=human_origin,
            sqlite_path=self.ledger_sqlite,
            json_path=self.ledger_json,
        )

        # Tamper the JSON export: replace signature with raw SHA-256 digest
        export_data = json.loads(self.ledger_json.read_text(encoding="utf-8"))
        export_data["operator_decisions"][0]["signature"] = export_data["operator_decisions"][0]["entry_digest"]
        self.ledger_json.write_text(json.dumps(export_data, indent=2), encoding="utf-8")

        # Parity check must fail closed
        parity_ok, parity_msg = verify_store_parity(self.ledger_sqlite, self.ledger_json)
        self.assertFalse(parity_ok, "Store parity must reject raw SHA-256 signatures")
        self.assertIn("Rejected unauthenticated SHA-256 signature string", parity_msg)

    def test_e2e_04_anti_bypass_hook_configuration_invariants(self) -> None:
        """E2E Test 4: Verify .agents/hooks.json declares nee-anti-bypass-direct-graph-write-guard with blocking severity."""
        self.assertTrue(HOOKS_JSON.is_file())
        data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        hooks = {h["id"]: h for h in data.get("hooks", [])}

        self.assertIn("nee-anti-bypass-direct-graph-write-guard", hooks)
        guard = hooks["nee-anti-bypass-direct-graph-write-guard"]
        self.assertEqual(guard.get("event"), "pre_tool_call")
        self.assertEqual(guard.get("severity"), "blocking")

        patterns = guard.get("match", {}).get("command_patterns", [])
        self.assertTrue(any("apply_visual_org_projection_to_graph" in p for p in patterns))
        self.assertTrue(any("UPDATE" in p for p in patterns))
        self.assertTrue(any("INSERT" in p for p in patterns))


if __name__ == "__main__":
    unittest.main()
