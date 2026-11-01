"""Wave 2 E2E test for document entity-mention stub suppression and predicate fidelity in graph change sets."""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.core import ROOT
from dual_pipeline.document_pipeline_core.entity_canonicalizer import (
    canonicalize_projection_entities,
)
from dual_pipeline.document_pipeline_core.sow_contract_extractor import (
    extract_sow_contract_projection,
)
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake import build_dispositions, build_graph_change_set
from dual_pipeline.normalization import normalize_evidence_envelope


def _find_baseline_graph() -> Path:
    candidates = [
        ROOT / "graph" / "nee_graph.sqlite",
        ROOT / ".local" / "dual_pipeline" / "generations" / "generation-36b538f04af06cb4be863517d4f0" / "graph.sqlite",
        ROOT.parent.parent / "graph" / "nee_graph.sqlite",
    ]
    found = next((g for g in candidates if g.is_file()), None)
    assert found is not None, f"Baseline graph not found in {candidates}"
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


def test_document_sow_change_set_creates_all_nodes_with_zero_stub_assertions_e2e() -> None:
    """Verify a document SOW bundle creates 100% of new nodes while emitting 0 'is an identified' HAS_CLAIM_TEXT stubs."""
    sow_md_path = ROOT / "output/hrit/10-19-2026/Nee4u_Employee_Experience_Align_PartnerAdvisory/source_document.txt"
    sow_text = sow_md_path.read_text(encoding="utf-8")
    proj = canonicalize_projection_entities(extract_sow_contract_projection(sow_text))

    with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp_dir:
        db_path = Path(tmp_dir) / "test_doc_graph.sqlite"
        shutil.copy2(_find_baseline_graph(), db_path)

        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-document-enterprise4u-sow-test",
            "meeting": {
                "meeting_id": "document-20261019-enterprise4u-align-prf-sow",
                "title": "Document 20261019 enterprise4u Employee Experience Align Prf Integration Sow",
                "started_at": "2026-10-19T14:09:00-04:00",
                "source_location": "output/hrit/10-19-2026/Nee4u_Employee_Experience_Align_PartnerAdvisory/source_document.txt",
                "source_hash": "d" * 64,
                "attendees": ["Drew Oakley", "Clement Hamel", "Beau Williamson"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.7",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-dan-okray",
                    "reported_name": "Drew Oakley",
                    "email": "dan.okray@consulting.example.com",
                    "role_or_org": "PartnerAdvisory Managing Director",
                    "statements": [
                        {
                            "statement_id": eid,
                            "kind": "technical",
                            "text": str(ev.get("text") or ev.get("exact_text") or "Contractual SOW statement across workstreams."),
                            "locator": {"kind": "section", "value": "Section 1"},
                            "gemini_confidence": 0.95,
                        }
                        for eid, ev in list(proj["evidence_index"].items())
                    ],
                }
            ],
            "actions": [],
            "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": proj,
            },
        }
        sealed_pkg = seal_artifact(package)
        bundle = normalize_evidence_envelope(sealed_pkg, run_context="doc_sow_e2e")
        resolutions = resolve_bundle(bundle, db_path)
        routes = _build_routes(bundle)
        dispositions = build_dispositions(bundle, routes, resolutions)

        conn_gov = sqlite3.connect(db_path)
        conn_gov.execute(
            """INSERT OR REPLACE INTO governance_approval
               (approval_id, approval_reference, approval_type, scope_json,
                proposal_sha256, approval_statement_sha256, approved_at,
                status, approved_by, release_version, active, created_at)
               VALUES ('approval-doc-sow', 'approval-doc-sow', 'ingestion_promotion', ?,
                       ?, ?, '2026-10-19T14:09:00Z', 'approved', 'operator', '1.0.0', 1, '2026-10-19T14:09:00Z')""",
            (json.dumps({"allowed_actions": ["canonical_assertion_promotion"]}), "0" * 64, "0" * 64),
        )
        conn_gov.commit()
        conn_gov.close()

        change_set = build_graph_change_set(
            bundle=bundle,
            dispositions=dispositions,
            resolutions=resolutions,
            graph_path=db_path,
        )
        assert change_set is not None
        ops = change_set["operations"]
        create_ops = [op for op in ops if op["operation"] == "create_entity"]
        rel_ops = [op for op in ops if op["operation"] == "add_relationship"]
        stub_ops = [
            op
            for op in ops
            if op["operation"] in ("add_candidate_assertion", "add_approved_assertion")
            and "is an identified" in str(
                change_set["extensions"]["com.partner_advisory.nee.operation-payloads"][op["operation_id"]]["candidate"].get("normalized_text") or ""
            )
        ]
        assert len(create_ops) >= 35
        assert len(rel_ops) >= 55
        assert len(stub_ops) == 0

        change_set["status"] = "approved"
        change_set["approval_id"] = "approval-doc-sow"
        change_set = seal_artifact(change_set)
        validate_artifact(change_set)

        write_res = GovernedGraphWriter(db_path).apply(change_set)
        assert write_res["physical_proof"]["overall"] == "PASS"

        ro_conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        try:
            src_id = bundle["source_id"]
            stub_db_count = ro_conn.execute(
                "SELECT COUNT(*) FROM assertion a JOIN assertion_evidence ae ON a.assertion_id=ae.assertion_id "
                "JOIN evidence_item e ON ae.evidence_id=e.evidence_id "
                "WHERE e.source_id=? AND a.predicate='HAS_CLAIM_TEXT' AND a.object_literal LIKE '%is an identified%'",
                (src_id,),
            ).fetchone()[0]
            assert stub_db_count == 0

            rel_db_count = ro_conn.execute(
                "SELECT COUNT(DISTINCT a.assertion_id) FROM assertion a JOIN assertion_evidence ae ON a.assertion_id=ae.assertion_id "
                "JOIN evidence_item e ON ae.evidence_id=e.evidence_id "
                "WHERE e.source_id=? AND a.predicate != 'HAS_CLAIM_TEXT'",
                (src_id,),
            ).fetchone()[0]
            assert rel_db_count >= 55

            # Verify relational predicates in DB and predicate alias mapping
            preds = {
                row[0]
                for row in ro_conn.execute(
                    "SELECT DISTINCT a.predicate FROM assertion a JOIN assertion_evidence ae ON a.assertion_id=ae.assertion_id "
                    "JOIN evidence_item e ON ae.evidence_id=e.evidence_id WHERE e.source_id=?",
                    (src_id,),
                ).fetchall()
            }
            assert "OWNS_OR_LEADS" in preds
            assert "AFFILIATED_WITH" in preds
            assert "DEPENDS_ON" in preds
            assert "CONTAINS" in preds
            assert "DELIVERS" in preds
            assert "ROUTES_TO" in preds
        finally:
            ro_conn.close()

    from dual_pipeline.intake_core.change_set_helpers import resolve_relationship_predicate

    assert resolve_relationship_predicate({}, "c-1", {"c-1": {"relationship_predicate": "HAS_ROLE"}}) == "HAS_ROLE_IN"
    assert resolve_relationship_predicate({}, "c-2", {"c-2": {"relationship_predicate": "USES_SYSTEM"}}) == "DEPENDS_ON"
    assert resolve_relationship_predicate({}, "c-3", {"c-3": {"relationship_predicate": "HAS_METRIC"}}) == "CONTAINS"


def test_meeting_bundle_preserves_entity_mention_behavior_e2e() -> None:
    """Verify non-document (meeting-*) bundles still emit standard entity_mention assertions."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp_dir:
        db_path = Path(tmp_dir) / "test_mtg_graph.sqlite"
        shutil.copy2(_find_baseline_graph(), db_path)

        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-meeting-stub-check",
            "meeting": {
                "meeting_id": "meeting-20261019-architecture-sync",
                "title": "Architecture Sync",
                "started_at": "2026-10-19T14:09:00-04:00",
                "source_location": "output/hrit/10-19-2026/Architecture_Sync/source_transcript.txt",
                "source_hash": "e" * 64,
                "attendees": ["Drew Oakley"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.7",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-dan-okray",
                    "reported_name": "Drew Oakley",
                    "email": "dan.okray@consulting.example.com",
                    "role_or_org": "Engagement Lead",
                    "statements": [
                        {
                            "statement_id": "stmt-01",
                            "kind": "technical",
                            "text": "We are reviewing the HR 360 integration with the enterprise gateway.",
                            "locator": {"kind": "timestamp", "value": "00:01"},
                            "gemini_confidence": 0.92,
                        }
                    ],
                }
            ],
            "actions": [],
            "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "entity_upserts": [
                        {
                            "id": "system:hr-360-test-mtg",
                            "type": "system",
                            "name": "HR 360",
                            "description": "HR 360 portal",
                            "mutation": "CREATE",
                        }
                    ],
                    "relationship_signals": [],
                }
            },
        }
        sealed_pkg = seal_artifact(package)
        bundle = normalize_evidence_envelope(sealed_pkg, run_context="mtg_stub_e2e")
        resolutions = resolve_bundle(bundle, db_path)
        routes = _build_routes(bundle)
        dispositions = build_dispositions(bundle, routes, resolutions)
        change_set = build_graph_change_set(
            bundle=bundle,
            dispositions=dispositions,
            resolutions=resolutions,
            graph_path=db_path,
        )
        assert change_set is not None
        assert any(
            op["operation"] in ("add_candidate_assertion", "add_approved_assertion")
            for op in change_set["operations"]
        )
