"""Adversarial test suite for direct graph mutation bypass prevention and signature rigor.

Wave 5 Governance:
1. Invariant bypass attempt: Bypassing UWG by calling apply_visual_org_projection_to_graph
   without an approval row or with a "0"*64 dummy approval fails closed (PermissionError).
2. Malformed input: Malformed UWG receipts (invalid SHA-256 length, "0"*64 digest) fail closed.
3. Invariant bypass attempt: Recording or validating an operator decision with a raw SHA-256
   string masquerading as an Ed25519 signature fails closed.
4. Exact production regression: Session transcript bypass command strings are intercepted
   by nee-anti-bypass-direct-graph-write-guard in .agents/hooks.json.
5. Boundary payload: Empty projection and missing private key fail closed without corrupting graph.
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
    _sign_decision,
    validate_ledger_signature,
)
from dual_pipeline.vector_indexing.bge_m3_org_indexer import (
    apply_visual_org_projection_to_graph,
)
from storage.connection import configure_pragmas, create_sqlite_conn

ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_JSON = ROOT / ".agents" / "hooks.json"


@pytest.mark.timeout(60)
class DirectGraphMutationBypassAdversarialTests(unittest.TestCase):
    """Adversarial test suite verifying fail-closed invariants against direct graph mutation bypass."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="adv_bypass_")
        self.tmp_path = Path(self.temp_dir.name)
        self.blank_db = self.tmp_path / "blank_graph.sqlite"
        self._init_blank_graph_db(self.blank_db)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_blank_graph_db(self, db_path: Path) -> None:
        """Create a graph DB schema without pre-existing approvals."""
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS source (
                source_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_date TEXT,
                authority_rank INTEGER,
                source_posture TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_version (
                source_version_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                version_number INTEGER,
                source_fingerprint TEXT,
                content_hash TEXT,
                source_date_precision TEXT,
                source_date_status TEXT,
                authority_rank INTEGER,
                scope_text TEXT,
                posture_text TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS governance_approval (
                approval_id TEXT PRIMARY KEY,
                approval_reference TEXT NOT NULL,
                approval_type TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                proposal_sha256 TEXT,
                approval_statement_sha256 TEXT,
                approved_at TEXT,
                status TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                release_version TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                description TEXT,
                status TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS edge (
                edge_id TEXT PRIMARY KEY,
                from_node_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                to_node_id TEXT NOT NULL,
                description TEXT,
                evidence_posture TEXT,
                confidence TEXT,
                materiality TEXT,
                source_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                assertion_fingerprint TEXT NOT NULL,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT NOT NULL,
                assertion_type TEXT NOT NULL,
                atomicity_state TEXT NOT NULL,
                scope_text TEXT,
                extraction_method TEXT,
                origin_type TEXT,
                confidence_label TEXT,
                confidence_score REAL,
                validation_state TEXT,
                currentness_state TEXT,
                date_precision TEXT,
                date_status TEXT,
                recorded_at TEXT,
                resolution_state TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                retrieval_eligible INTEGER,
                materiality TEXT,
                classification TEXT,
                allowed_audiences_json TEXT,
                hitl_required INTEGER,
                qualifiers_json TEXT,
                legacy_record_type TEXT,
                legacy_record_ids_json TEXT,
                primary_source_id TEXT,
                approval_reference TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_item (
                evidence_id TEXT PRIMARY KEY,
                evidence_fingerprint TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_version_id TEXT NOT NULL,
                locator_kind TEXT,
                locator_value TEXT,
                direct_quote TEXT,
                evidence_directness TEXT,
                source_authority_rank INTEGER,
                extraction_confidence REAL,
                location_status TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assertion_evidence (
                assertion_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                evidence_relationship TEXT NOT NULL,
                independent_corroboration INTEGER,
                created_at TEXT NOT NULL,
                PRIMARY KEY (assertion_id, evidence_id)
            );
            CREATE TABLE IF NOT EXISTS search_document (
                search_document_id TEXT PRIMARY KEY,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                assertion_id TEXT NOT NULL,
                title TEXT,
                body TEXT,
                canonical_status TEXT,
                currentness_state TEXT,
                retrieval_eligible INTEGER,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()

    def test_adv_vector_01_direct_mutation_without_approval_fails_closed(self) -> None:
        """Category: Invariant bypass attempt. Direct mutation without approval raises PermissionError."""
        dummy_proj: Dict[str, Any] = {
            "graph_mutations": {
                "entity_upserts": [{"id": "p_bob", "name": "Bob", "type": "person"}],
                "relationship_signals": [],
            }
        }
        with self.assertRaises(PermissionError) as ctx:
            apply_visual_org_projection_to_graph(self.blank_db, dummy_proj)
        self.assertIn("Governed mutation rejected", str(ctx.exception))

    def test_adv_vector_02_zero_hash_dummy_approval_fails_closed(self) -> None:
        """Category: Malformed input / bypass attempt. Zero-hash approval in DB or receipt is rejected."""
        dummy_proj: Dict[str, Any] = {
            "graph_mutations": {
                "entity_upserts": [{"id": "p_bob", "name": "Bob", "type": "person"}],
                "relationship_signals": [],
            }
        }
        # Insert a zero-hash approval into DB
        conn = sqlite3.connect(self.blank_db)
        conn.execute(
            """INSERT INTO governance_approval (
                   approval_id, approval_reference, approval_type, scope_json,
                   proposal_sha256, approval_statement_sha256, approved_at,
                   status, approved_by, release_version, active, created_at
               ) VALUES ('app_zero', 'AD-ZERO', 'ingestion', '{}', ?, ?, '2026-10-09T00:00:00Z', 'approved', 'op', '1.0', 1, '2026-10-09T00:00:00Z')""",
            ("0" * 64, "0" * 64),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(PermissionError) as ctx:
            apply_visual_org_projection_to_graph(self.blank_db, dummy_proj)
        self.assertIn("Governed mutation rejected", str(ctx.exception))

        # Pass zero-hash UWG receipt
        with self.assertRaises(PermissionError) as ctx:
            apply_visual_org_projection_to_graph(
                self.blank_db,
                dummy_proj,
                uwg_receipt={"approval_reference": "AD-ZERO", "proposal_sha256": "0" * 64},
            )
        self.assertIn("invalid UWG receipt", str(ctx.exception))

    def test_adv_vector_03_valid_uwg_receipt_authorizes_mutation(self) -> None:
        """Category: Invariant adherence. Valid non-zero UWG receipt succeeds and records approval."""
        dummy_proj: Dict[str, Any] = {
            "graph_mutations": {
                "entity_upserts": [{"id": "p_alice", "name": "Alice", "type": "person"}],
                "relationship_signals": [],
            }
        }
        valid_sha = "a" * 64
        receipt = {
            "approval_id": "app_real_001",
            "approval_reference": "AD-REAL-001",
            "proposal_sha256": valid_sha,
            "status": "approved",
        }
        res = apply_visual_org_projection_to_graph(self.blank_db, dummy_proj, uwg_receipt=receipt)
        self.assertEqual(res["nodes_upserted"], 1)

        conn = sqlite3.connect(self.blank_db)
        cur = conn.cursor()
        cur.execute("SELECT approval_reference, proposal_sha256 FROM governance_approval WHERE approval_id = 'app_real_001'")
        row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "AD-REAL-001")
        self.assertEqual(row[1], valid_sha)

    def test_adv_vector_04_raw_sha256_ledger_signature_rejected(self) -> None:
        """Category: Invariant bypass attempt. Ledger signing without key raises; raw SHA-256 rejected."""
        # 1. _sign_decision without private key raises RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            _sign_decision(
                entry_id="entry_01",
                dec_id="dec_01",
                seq=0,
                digest_val="b" * 64,
                op_id="human_operator",
                priv_key=None,
            )
        self.assertIn("missing Ed25519 authority private key", str(ctx.exception))

        # 2. validate_ledger_signature rejects entry where signature == entry_digest
        fake_entry = {
            "entry_id": "entry_01",
            "decision_id": "dec_01",
            "sequence_number": 0,
            "entry_digest": "b" * 64,
            "signature": "b" * 64,
            "operator": "human_operator",
        }
        self.assertFalse(validate_ledger_signature(fake_entry))

        # 3. validate_ledger_signature rejects arbitrary 64-hex strings
        fake_entry_hex = {
            "entry_id": "entry_01",
            "decision_id": "dec_01",
            "sequence_number": 0,
            "entry_digest": "b" * 64,
            "signature": "c" * 64,
            "operator": "human_operator",
        }
        self.assertFalse(validate_ledger_signature(fake_entry_hex))

    def test_adv_vector_05_authentic_ed25519_signature_passes(self) -> None:
        """Category: Cryptographic contract. Legitimate Ed25519 signature over RFC 8785 validates."""
        priv_key, pub_key = generate_hitl_keypair()
        entry_digest = "d" * 64
        sig = _sign_decision(
            entry_id="entry_02",
            dec_id="dec_02",
            seq=1,
            digest_val=entry_digest,
            op_id="human_operator",
            priv_key=priv_key,
        )
        self.assertEqual(len(sig), 86)  # Ed25519 unpadded base64url is 86 chars

        valid_entry = {
            "entry_id": "entry_02",
            "decision_id": "dec_02",
            "sequence_number": 1,
            "entry_digest": entry_digest,
            "signature": sig,
            "operator": "human_operator",
        }
        self.assertTrue(validate_ledger_signature(valid_entry, pub_key=pub_key))

    def test_adv_vector_06_exact_incident_replayed_commands_blocked_by_hook(self) -> None:
        """Category: Exact production regression. Replay session bypass commands against hooks.json patterns."""
        self.assertTrue(HOOKS_JSON.is_file(), ".agents/hooks.json must exist")
        hooks_data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        hooks_dict = {h["id"]: h for h in hooks_data.get("hooks", [])}
        self.assertIn("nee-anti-bypass-direct-graph-write-guard", hooks_dict)

        guard = hooks_dict["nee-anti-bypass-direct-graph-write-guard"]
        patterns = guard.get("match", {}).get("command_patterns", [])

        # Replay the exact transcript commands from incident (step #213-221)
        replayed_commands = [
            ".venv/bin/python -c 'from dual_pipeline.vector_indexing.bge_m3_org_indexer import apply_visual_org_projection_to_graph; apply_visual_org_projection_to_graph(\"graph/nee_graph.sqlite\", proj)'",
            "python -c 'import sqlite3; conn = sqlite3.connect(\"graph/nee_graph.sqlite\"); conn.execute(\"UPDATE assertion SET canonical_status = 1\"); conn.commit()'",
            "python -c 'from dual_pipeline.vector_indexing.bge_m3_org_indexer import apply_projection; apply_projection()'",
            "sqlite3 graph/nee_graph.sqlite \"INSERT INTO node VALUES ('1','2','3','4','5','6',1,'now','now')\"",
            "sqlite3 graph/nee_graph.sqlite \"UPDATE assertion SET canonical_status = 'canonical'\"",
        ]

        def _matches_any(cmd: str) -> bool:
            for p in patterns:
                if re.search(p, cmd):
                    return True
            return False

        for cmd in replayed_commands:
            self.assertTrue(
                _matches_any(cmd),
                f"Incident replayed command must be blocked by hook pattern: {cmd}",
            )


if __name__ == "__main__":
    unittest.main()
