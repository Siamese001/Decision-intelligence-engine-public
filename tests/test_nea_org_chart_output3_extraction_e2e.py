"""E2E test suite for NEE/NEA organizational structure extraction and pure Output 03 delivery."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.attempt_binding import _applicable_outputs
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.visual_org_extractor import extract_visual_org_projection

ROOT = Path(__file__).resolve().parent.parent
ENVELOPE_DIR = ROOT / "reports" / "visual_intake_envelopes"
MANIFEST_PATH = ENVELOPE_DIR / "manifest.json"


def test_pure_output3_delivery_suppresses_transcripts_and_recaps():
    """Verify that document visual extraction suppresses Output 01 and 02 delivery."""
    outputs_doc = _applicable_outputs(CanonicalModality.DOCUMENT, output3_only=True)
    assert outputs_doc == ("output_3",), "Document visual modality must only target Output 03"

    outputs_flagged = _applicable_outputs(CanonicalModality.MEETING_TRANSCRIPT, output3_only=True)
    assert outputs_flagged == ("output_3",), "output3_only flag must strictly target Output 03"



def test_extract_visual_org_projection_from_all_envelopes():
    """Verify signal extraction across all 8 visual document envelopes."""
    assert MANIFEST_PATH.exists(), f"Envelope manifest {MANIFEST_PATH} must exist"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert len(manifest) >= 8

    all_persons = set()
    all_orgs = set()
    all_reports_to = []

    for item in manifest:
        env_path = ROOT / item["envelope_path"]
        assert env_path.exists(), f"Envelope file {env_path} must exist"
        envelope = json.loads(env_path.read_text(encoding="utf-8"))

        projection = extract_visual_org_projection(envelope, project="nea")

        assert projection["schema_version"] == "5.7"
        assert projection["classification"] == "internal"
        assert projection["source_type"] == "visual_document"
        assert "evidence_index" in projection
        assert "graph_mutations" in projection

        mutations = projection["graph_mutations"]
        assert "entity_upserts" in mutations
        assert "relationship_signals" in mutations

        for entity in mutations["entity_upserts"]:
            assert "id" in entity
            assert "type" in entity
            assert "name" in entity
            assert entity["evidence_ids"], "Entity must be grounded with evidence IDs"
            if entity["type"] == "person":
                all_persons.add(entity["name"])
            elif entity["type"] == "org":
                all_orgs.add(entity["name"])

        for rel in mutations["relationship_signals"]:
            assert "signal_id" in rel
            assert "from_id" in rel
            assert "to_id" in rel
            assert "relationship" in rel
            assert rel["evidence_ids"], "Relationship must be grounded with evidence IDs"
            if rel["relationship"] == "REPORTS_TO":
                all_reports_to.append((rel["from_id"], rel["to_id"]))

    # Core invariant: 25+ distinct stakeholders extracted
    assert len(all_persons) >= 25, f"Expected at least 25 persons, found {len(all_persons)}: {sorted(all_persons)}"
    assert len(all_orgs) >= 5, f"Expected organizational units, found {len(all_orgs)}"
    assert len(all_reports_to) >= 10, f"Expected hierarchy edges, found {len(all_reports_to)}"


def test_reporting_hierarchy_traversal_integrity():
    """Verify specific executive and engineering reporting chains."""
    # Cohort A Org Chart 1: Scott Herold -> Rianne Donst -> Chakra Sankaraiah -> Richard Argentieri
    env1_path = ENVELOPE_DIR / "org_chart_01_scott_herold_nee_analytics_envelope.json"
    env1 = json.loads(env1_path.read_text(encoding="utf-8"))
    proj1 = extract_visual_org_projection(env1)
    rels1 = {r["from_id"]: r["to_id"] for r in proj1["graph_mutations"]["relationship_signals"] if r["relationship"] == "REPORTS_TO"}

    assert rels1.get("person:scott-herold") == "person:rianne-donst"
    assert rels1.get("person:rianne-donst") == "person:chakra-sankaraiah"
    assert rels1.get("person:chakra-sankaraiah") == "person:richard-argentieri"

    # Cohort A Org Chart 2: Jennifer Belger -> Javid Khan -> Mark Lemasney -> John Ketchum
    env2_path = ENVELOPE_DIR / "org_chart_02_jennifer_belger_op_excellence_envelope.json"
    env2 = json.loads(env2_path.read_text(encoding="utf-8"))
    proj2 = extract_visual_org_projection(env2)
    rels2 = {r["from_id"]: r["to_id"] for r in proj2["graph_mutations"]["relationship_signals"] if r["relationship"] == "REPORTS_TO"}

    assert rels2.get("person:jennifer-belger") == "person:javid-khan"
    assert rels2.get("person:javid-khan") == "person:mark-lemasney"
    assert rels2.get("person:mark-lemasney") == "person:john-ketchum"


def test_apm_portfolio_governance_relationships():
    """Verify FinAnalytics APM roster extraction and governance edges."""
    apm_env_path = ENVELOPE_DIR / "Speaker_Beta_FinAnalytics_Carlos_APM_Workflow_envelope.json"
    apm_env = json.loads(apm_env_path.read_text(encoding="utf-8"))
    proj = extract_visual_org_projection(apm_env)

    ents = {e["id"]: e for e in proj["graph_mutations"]["entity_upserts"]}
    assert "role:application-portfolio-manager" in ents
    assert "person:carlos-e-sanchez" in ents
    assert "person:ben-miron" in ents
    assert "person:josh-millar" in ents

    has_role_signals = [r for r in proj["graph_mutations"]["relationship_signals"] if r["relationship"] == "HAS_ROLE"]
    assert len(has_role_signals) >= 5

    governs_signals = [r for r in proj["graph_mutations"]["relationship_signals"] if r["relationship"] == "GOVERNS"]
    assert len(governs_signals) >= 5
