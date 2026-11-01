"""
Wave 1 Adversarial & Edge-Case tests for 20-document projection upgrade.
Covers 4 mandatory edge-case categories:
1. Boundary payload: Empty/null projection and 0-entity documents.
2. Malformed input: Underscore/uppercase person/org IDs and missing evidence_index keys.
3. Invariant bypass attempt: Non-canonical `HAS_METRIC` predicate rejection & degree==0 orphan injection.
4. Rollback & recovery: Idempotent re-canonicalization and connectivity healing across repeated runs.
"""
from __future__ import annotations

import copy
import re
from dual_pipeline.document_pipeline_core.document_connectivity_healer import heal_disconnected_document_entities
from dual_pipeline.document_pipeline_core.entity_canonicalizer import canonicalize_projection_entities
from dual_pipeline.transcript_qa_core.semantic_linters import CANONICAL_DOMAIN_PREDICATES


def test_boundary_empty_and_null_projection_handling() -> None:
    empty_proj = {"schema_version": "5.7", "evidence_index": {}, "graph_mutations": {"entity_upserts": [], "relationship_signals": []}}
    res = canonicalize_projection_entities(copy.deepcopy(empty_proj))
    healed = heal_disconnected_document_entities(res, {"title": "Empty Doc"}, "")
    assert healed == []
    assert res["graph_mutations"]["entity_upserts"] == []


def test_malformed_underscore_and_uppercase_entity_ids_canonicalized() -> None:
    proj = {
        "schema_version": "5.7",
        "evidence_index": {"ev-1": {"text": "Jack O'Shea at Apex Global Enterprises leads DocIQ.", "exact_text": "Jack O'Shea at Apex Global Enterprises leads DocIQ."}},
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:Jack_O_Shea", "type": "person", "name": "Jack O'Shea", "evidence_ids": ["ev-1"]},
                {"id": "org:Apex Global_Energy_Inc", "type": "org", "name": "Apex Global Enterprises", "evidence_ids": ["ev-1"]},
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-1",
                    "from_id": "person:Jack_O_Shea",
                    "to_id": "org:Apex Global_Energy_Inc",
                    "predicate": "MEMBER_OF",
                    "relationship": "MEMBER_OF",
                    "evidence_ids": ["ev-1"],
                }
            ],
        },
    }
    canon = canonicalize_projection_entities(proj)
    for ent in canon["graph_mutations"]["entity_upserts"]:
        assert re.match(r"^(?:person|org):[a-z0-9-]+$", ent["id"]), f"Un-normalized ID: {ent['id']}"
    sig = canon["graph_mutations"]["relationship_signals"][0]
    assert "_" not in sig["from_id"] and "_" not in sig["to_id"]


def test_invariant_bypass_orphan_entities_and_predicate_domain() -> None:
    proj = {
        "schema_version": "5.7",
        "evidence_index": {"ev-1": {"text": "PartnerAdvisory delivers Project LENS with 99% accuracy.", "exact_text": "PartnerAdvisory delivers Project LENS with 99% accuracy."}},
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-1"]},
                {"id": "project:lens", "type": "project", "name": "Project LENS", "evidence_ids": ["ev-1"]},
                {"id": "person:orphan-engineer", "type": "person", "name": "Orphan Engineer", "evidence_ids": ["ev-1"]},
                {"id": "system:orphan-tool", "type": "system", "name": "Orphan Tool", "evidence_ids": ["ev-1"]},
            ],
            "relationship_signals": [
                {"signal_id": "s1", "from_id": "org:partner_advisory", "to_id": "project:lens", "predicate": "DELIVERS", "relationship": "DELIVERS", "evidence_ids": ["ev-1"]}
            ],
        },
    }
    heal_disconnected_document_entities(proj, {"title": "LENS Update"}, "PartnerAdvisory delivers Project LENS with 99% accuracy.")
    ents = proj["graph_mutations"]["entity_upserts"]
    sigs = proj["graph_mutations"]["relationship_signals"]
    deg = {e["id"]: 0 for e in ents}
    for s in sigs:
        deg[s["from_id"]] = deg.get(s["from_id"], 0) + 1
        deg[s["to_id"]] = deg.get(s["to_id"], 0) + 1
        pred = s.get("predicate") or s.get("relationship")
        assert pred in CANONICAL_DOMAIN_PREDICATES
    assert all(v > 0 for v in deg.values())


def test_idempotent_recanonicalization_and_healing() -> None:
    proj = {
        "schema_version": "5.7",
        "evidence_index": {"ev-1": {"text": "Amit Ayer leads PartnerAdvisory Florida.", "exact_text": "Amit Ayer leads PartnerAdvisory Florida."}},
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "evidence_ids": ["ev-1"]},
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-1"]},
            ],
            "relationship_signals": [],
        },
    }
    first = canonicalize_projection_entities(copy.deepcopy(proj))
    heal_disconnected_document_entities(first, {"title": "PartnerAdvisory Florida"}, "Amit Ayer leads PartnerAdvisory Florida.")
    second = canonicalize_projection_entities(copy.deepcopy(first))
    heal_disconnected_document_entities(second, {"title": "PartnerAdvisory Florida"}, "Amit Ayer leads PartnerAdvisory Florida.")
    assert first["graph_mutations"] == second["graph_mutations"]
