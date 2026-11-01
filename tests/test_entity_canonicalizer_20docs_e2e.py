"""End-to-end tests for Wave 2: Universal Entity Slug Canonicalization & Nickname/Org Deduplication."""
from __future__ import annotations

from dual_pipeline.document_pipeline_core.entity_canonicalizer import (
    canonicalize_projection_entities,
    resolve_canonical_person,
)


def test_underscore_and_inverted_person_ids_canonicalize_to_hyphenated() -> None:
    proj = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:clement_hamel", "type": "person", "name": "Clement Hamel", "evidence_ids": ["ev-1"]},
                {"id": "person:jack_o_shea", "type": "person", "name": "Jack O'Shea", "evidence_ids": ["ev-2"]},
                {"id": "person:stephan_marceau", "type": "person", "name": "Cameron Mercer", "evidence_ids": ["ev-3"]},
                {"id": "person:miles_erickson", "type": "person", "name": "Miles Erickson", "evidence_ids": ["ev-4"]},
                {"id": "person:skyler_carlson", "type": "person", "name": "Skyler Carlson", "evidence_ids": ["ev-5"]},
                {"id": "person:medina_jose", "type": "person", "name": "Jose Medina", "evidence_ids": ["ev-6"]},
                {"id": "person:diana_chen", "type": "person", "name": "Diana Chen", "evidence_ids": ["ev-7"]},
                {"id": "org:apex_global", "type": "org", "name": "Apex Global", "evidence_ids": ["ev-2"]},
                {"id": "org:next era-energy", "type": "org", "name": "Apex Global Enterprises", "evidence_ids": ["ev-5"]},
            ],
            "relationship_signals": [
                {"signal_id": "s1", "from_id": "person:jack_o_shea", "to_id": "org:apex_global", "predicate": "MEMBER_OF", "evidence_ids": ["ev-2"]},
                {"signal_id": "s2", "from_id": "person:skyler_carlson", "to_id": "org:next era-energy", "predicate": "MEMBER_OF", "evidence_ids": ["ev-5"]},
            ],
        }
    }
    res = canonicalize_projection_entities(proj)
    upserts = res["graph_mutations"]["entity_upserts"]
    ids = {e["id"] for e in upserts}
    assert "person:clement-hamel" in ids
    assert "person:jack-o-shea" in ids
    assert "person:stephan-marceau" in ids
    assert "person:miles-erickson" in ids
    assert "person:skyler-carlson" in ids
    assert "person:jose-medina" in ids
    assert "person:diana-chen" in ids
    assert "org:apex_global-energy" in ids
    assert "org:apex_global" not in ids
    assert "org:next era-energy" not in ids
    assert all("_" not in eid and " " not in eid for eid in ids)

    rels = res["graph_mutations"]["relationship_signals"]
    assert {(r["from_id"], r["to_id"], r["predicate"]) for r in rels} == {
        ("person:jack-o-shea", "org:apex_global-energy", "MEMBER_OF"),
        ("person:skyler-carlson", "org:apex_global-energy", "MEMBER_OF"),
    }


def test_middle_nickname_and_single_token_merges() -> None:
    proj = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:eduardo-ed-juan-more", "type": "person", "name": 'Eduardo "Ed" Juan More', "evidence_ids": ["ev-a"]},
                {"id": "person:eduardo-juan-more", "type": "person", "name": "Eduardo Juan More", "evidence_ids": ["ev-b"]},
                {"id": "person:ed-more", "type": "person", "name": "Ed More", "evidence_ids": ["ev-c"]},
                {"id": "person:dan", "type": "person", "name": "Dan", "evidence_ids": ["ev-d"]},
            ],
            "relationship_signals": [
                {"signal_id": "s1", "from_id": "person:eduardo-ed-juan-more", "to_id": "person:dan", "predicate": "COLLABORATES_WITH", "evidence_ids": ["ev-a"]},
            ],
        }
    }
    res = canonicalize_projection_entities(proj)
    by_id = {e["id"]: e for e in res["graph_mutations"]["entity_upserts"]}
    assert "person:eduardo-juan-more" in by_id
    assert "person:eduardo-ed-juan-more" not in by_id
    assert "person:ed-more" not in by_id
    assert set(by_id["person:eduardo-juan-more"]["evidence_ids"]) == {"ev-a", "ev-b", "ev-c"}
    assert "person:dan-okray" in by_id
    rels = res["graph_mutations"]["relationship_signals"]
    assert rels[0]["from_id"] == "person:eduardo-juan-more"
    assert rels[0]["to_id"] == "person:dan-okray"


def test_resolve_canonical_person_direct_calls() -> None:
    assert resolve_canonical_person("person:clement_hamel", "Clement Hamel") == ("person:clement-hamel", "Clement Hamel")
    assert resolve_canonical_person("person:eduardo-ed-juan-more", 'Eduardo (Ed) Juan More') == (
        "person:eduardo-juan-more",
        "Eduardo Juan More",
    )
