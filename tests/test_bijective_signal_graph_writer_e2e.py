"""End-to-end verification of Wave 2 Gate C2 Bijective Signal-to-Graph Writer & Canonical Promotion."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Dict

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.core import ROOT
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.graph_writer_core.proof_gate import validate_bijective_signal_conservation
from dual_pipeline.graph_writer_core.sources import sanitize_cold_archive_ref
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake import build_dispositions, build_graph_change_set
from dual_pipeline.normalization import normalize_evidence_envelope


def _find_baseline_graph() -> Path:
    candidates = [
        ROOT / "graph" / "nee_graph.sqlite",
        ROOT.parent.parent / "graph" / "nee_graph.sqlite",
    ]
    found = next((g for g in candidates if g.is_file()), None)
    assert found is not None
    return found


def _build_routes(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "subject_id": c["candidate_id"],
            "intake_route": (
                "NEW_RELATIONSHIP"
                if c["candidate_type"] == "architecture_relationship"
                else "NEW_ENTITY"
                if c["candidate_type"] == "entity_mention"
                else "ACTION_ITEM"
                if c["candidate_type"] == "action"
                else "NEW_ASSERTION"
            ),
            "reason_codes": [],
        }
        for c in bundle["candidates"]
    ]


def test_bijective_signal_translation_preserves_substantive_descriptions_and_all_families_e2e() -> None:
    """Verify entity descriptions, relationships, decisions, proposals, risks, actions,战略 intents, and metrics translate without vacuous stubs."""
    proj: Dict[str, Any] = {
        "schema_version": "5.7",
        "evidence_index": {
            "ev-01": {
                "text": "Pitch & Rotor Total: 123 assets (38.2% of 322 Grand Total).",
                "exact_text": "Pitch & Rotor Total: 123 assets (38.2% of 322 Grand Total).",
                "locator": {"kind": "section", "value": "Section 1"},
            },
            "ev-02": {
                "text": "UI-only OnInit/OnChange rules do not run for API saves between HR 360 and SuccessFactors.",
                "exact_text": "UI-only OnInit/OnChange rules do not run for API saves between HR 360 and SuccessFactors.",
                "locator": {"kind": "section", "value": "Section 2"},
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "metric:pitch-and-rotor-asset-count",
                    "type": "metric",
                    "name": "Pitch & Rotor Total",
                    "description": "123 assets representing 38.2% of the 322 Grand Total asset inventory.",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "id": "system:hr-360-gateway",
                    "type": "system",
                    "name": "HR 360 Gateway",
                    "description": "Source-side integration gateway feeding SuccessFactors Position APIs.",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev-02"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-hr360-measures-pitch",
                    "from_id": "system:hr-360-gateway",
                    "to_id": "metric:pitch-and-rotor-asset-count",
                    "relationship": "CONTAINS",
                    "predicate": "CONTAINS",
                    "evidence_ids": ["ev-01"],
                }
            ],
            "decision_updates": [
                {
                    "decision_id": "decision:onsave-migration",
                    "title": "Move Position field derivation logic to SuccessFactors OnSave rule.",
                    "evidence_ids": ["ev-02"],
                }
            ],
            "proposal_updates": [
                {
                    "proposal_id": "proposal:batch-sync",
                    "statement": "Implement automated nightly field mapping reconciliation.",
                    "evidence_ids": ["ev-02"],
                }
            ],
            "risk_updates": [
                {
                    "risk_id": "risk:api-save-gap",
                    "rationale": "OnInit rules fail to trigger during headless API saves.",
                    "evidence_ids": ["ev-02"],
                }
            ],
            "action_updates": [
                {
                    "action_id": "act-audit-87-fields",
                    "description": "Audit all 87 Position fields in SuccessFactors sandbox.",
                    "evidence_ids": ["ev-02"],
                }
            ],
            "strategic_intent_signals": [
                {
                    "signal_id": "strat-zero-loss",
                    "value_case": "Eliminate manual HR position entry discrepancies across enterprise.",
                    "evidence_ids": ["ev-02"],
                }
            ],
            "metrics": [
                {
                    "id": "metric-grand-total-322",
                    "description": "Grand Total asset count is 322 (100.0%).",
                    "evidence_ids": ["ev-01"],
                }
            ],
        },
    }

    with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp_dir:
        db_path = Path(tmp_dir) / "test_bijective_graph.sqlite"
        shutil.copy2(_find_baseline_graph(), db_path)

        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-bijective-wave2-test",
            "meeting": {
                "meeting_id": "document-20261026-bijective-test",
                "title": "Document 20261026 Bijective Test",
                "started_at": "2026-10-26T14:00:00-04:00",
                "source_location": ".staging_Bijective_Test_81b351c0/bijective_test.md",
                "source_hash": "b" * 64,
                "attendees": ["Amit Ayer"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.7",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-amit-ayer",
                    "reported_name": "Amit Ayer",
                    "email": "amit.ayer@consulting.example.com",
                    "role_or_org": "PartnerAdvisory",
                    "statements": [
                        {
                            "statement_id": eid,
                            "kind": "technical",
                            "text": ev["text"],
                            "locator": ev["locator"],
                            "gemini_confidence": 0.95,
                        }
                        for eid, ev in proj["evidence_index"].items()
                    ],
                }
            ],
            "actions": [],
            "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": proj,
                "com.partner_advisory.nee.authority-rank": 9,
                "com.partner_advisory.nee.source-posture": "authoritative_governed",
            },
        }
        sealed_pkg = seal_artifact(package)
        bundle = normalize_evidence_envelope(sealed_pkg, run_context="wave2_bijective_e2e")
        cand_texts = [c["normalized_text"] for c in bundle["candidates"]]
        assert any("123 assets representing 38.2%" in t for t in cand_texts)
        assert any("Move Position field derivation logic" in t for t in cand_texts)
        assert any("Implement automated nightly field mapping" in t for t in cand_texts)
        assert any("OnInit rules fail to trigger" in t for t in cand_texts)
        assert any("Audit all 87 Position fields" in t for t in cand_texts)
        assert any("Eliminate manual HR position entry" in t for t in cand_texts)
        assert any("Grand Total asset count is 322" in t for t in cand_texts)
        assert not any("is an identified" in t for t in cand_texts if "Pitch & Rotor Total" in t)

        conn_gov = sqlite3.connect(db_path)
        conn_gov.execute(
            """INSERT OR REPLACE INTO governance_approval
               (approval_id, approval_reference, approval_type, scope_json,
                proposal_sha256, approval_statement_sha256, approved_at,
                status, approved_by, release_version, active, created_at)
               VALUES ('approval-wave2', 'approval-wave2', 'ingestion_promotion', ?,
                       ?, ?, '2026-10-26T14:00:00Z', 'approved', 'operator', '1.0.0', 1, '2026-10-26T14:00:00Z')""",
            (json.dumps({"allowed_actions": ["canonical_assertion_promotion"]}), "0" * 64, "0" * 64),
        )
        conn_gov.commit()
        conn_gov.close()

        resolutions = resolve_bundle(bundle, db_path)
        routes = _build_routes(bundle)
        dispositions = build_dispositions(bundle, routes, resolutions)
        change_set = build_graph_change_set(bundle, dispositions, resolutions, db_path)
        assert change_set is not None

        c2_receipt = validate_bijective_signal_conservation(proj, change_set)
        assert c2_receipt["status"] == "PASS"
        assert c2_receipt["input_auxiliary_signals"] == 6

        change_set["status"] = "approved"
        change_set["approval_id"] = "approval-wave2"
        change_set = seal_artifact(change_set)
        validate_artifact(change_set)

        write_res = GovernedGraphWriter(db_path).apply(change_set)
        assert write_res["physical_proof"]["overall"] == "PASS"

        ro_conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        try:
            src_row = ro_conn.execute(
                "SELECT cold_archive_ref FROM source WHERE source_id=?", (bundle["source_id"],)
            ).fetchone()
            assert src_row is not None
            assert not str(src_row[0]).startswith(".staging_")
            assert sanitize_cold_archive_ref(".staging_Aruna_81b351c0/email.md") == "output/Aruna/email.md"
        finally:
            ro_conn.close()
