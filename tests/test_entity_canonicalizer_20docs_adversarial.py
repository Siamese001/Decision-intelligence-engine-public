"""Adversarial edge-case tests for Wave 2: Universal Entity Slug Canonicalization & Nickname/Org Deduplication."""
from __future__ import annotations

from dual_pipeline.document_pipeline_core.entity_canonicalizer import (
    canonicalize_projection_entities,
    resolve_canonical_non_person_entity,
    resolve_canonical_person,
)


def test_boundary_empty_and_non_dict_mutations() -> None:
    assert canonicalize_projection_entities({}) == {}
    assert canonicalize_projection_entities({"graph_mutations": None}) == {"graph_mutations": None}
    assert canonicalize_projection_entities({"graph_mutations": {"entity_upserts": [None, "bad"]}}) == {
        "graph_mutations": {"entity_upserts": [], "relationship_signals": []}
    }


def test_self_loop_suppression_after_alias_merge() -> None:
    proj = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:matt-barrows", "type": "person", "name": "Matt Barrows", "evidence_ids": ["e1"]},
                {"id": "person:matthew-barrows", "type": "person", "name": "Matthew Barrows", "evidence_ids": ["e2"]},
            ],
            "relationship_signals": [
                {
                    "signal_id": "self-rel",
                    "from_id": "person:matt-barrows",
                    "to_id": "person:matthew-barrows",
                    "predicate": "COLLABORATES_WITH",
                    "evidence_ids": ["e1"],
                }
            ],
        }
    }
    res = canonicalize_projection_entities(proj)
    assert len(res["graph_mutations"]["entity_upserts"]) == 1
    assert res["graph_mutations"]["relationship_signals"] == []


def test_duplicate_relationship_deduplication_and_evidence_union() -> None:
    proj = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:jack_o_shea", "type": "person", "name": "Jack O'Shea", "evidence_ids": ["e1"]},
                {"id": "org:apex_global", "type": "org", "name": "Apex Global", "evidence_ids": ["e1"]},
                {"id": "org:next era-energy", "type": "org", "name": "Apex Global Enterprises", "evidence_ids": ["e2"]},
            ],
            "relationship_signals": [
                {"signal_id": "r1", "from_id": "person:jack_o_shea", "to_id": "org:apex_global", "predicate": "MEMBER_OF", "evidence_ids": ["e1"]},
                {"signal_id": "r2", "from_id": "person:jack-o-shea", "to_id": "org:next era-energy", "predicate": "MEMBER_OF", "evidence_ids": ["e2"]},
            ],
        }
    }
    res = canonicalize_projection_entities(proj)
    rels = res["graph_mutations"]["relationship_signals"]
    assert len(rels) == 1
    assert set(rels[0]["evidence_ids"]) == {"e1", "e2"}


def test_malformed_symbol_only_identifiers_fallback_cleanly() -> None:
    cid, cname = resolve_canonical_person("person:___", "   ")
    assert cid == "person:unknown"
    ncid, _ = resolve_canonical_non_person_entity("system:$$$", "system", "")
    assert ncid == "system:unknown"
