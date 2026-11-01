"""Adversarial & Edge-Case Tests for Wave 4 (Stage 4: C-16 Entity Canonicalization & In-Band Dedup).

Covers defects D-04-01 through D-04-12 across:
1. Boundary payload (empty/null projections, missing evidence_index, minimal schemas)
2. Malformed input (malformed locators, non-dict entities, missing confidence columns)
3. Invariant bypass attempt (cross-tenant bare 'Dan'/'Tyler' leakage, 'D. Chan'/'J. Chen'/'Roy' false merges, HITL bypass)
4. Concurrency / cache invalidation (audit_dedup stage_context & in-place file stat mtime_ns/size invalidation)
5. Exact production regression (28-char workstream prefix collision, 3-way transitive supersession chain, page:1 distinct quotes)
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.document_pipeline_core.audit_dedup import (
    get_healing_counters,
    get_validation_counters,
    healed_output_3_cached,
    reset_healing_counters,
    reset_validation_counters,
    validate_pair_cached,
)
from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    _compute_degrees,
    heal_disconnected_document_entities,
)
from dual_pipeline.document_pipeline_core.entity_canonicalizer import (
    canonicalize_projection_entities,
    deduplicate_projection_evidence_and_signals,
    resolve_canonical_non_person_entity,
    resolve_canonical_person,
)
from dual_pipeline.graph_maintenance_core.dedup import (
    apply_supersessions,
    find_semantic_duplicates,
)
from tools.tier2_scan4_evidence_dedup import scan_evidence_fragmentation


def test_d04_01_cross_tenant_bare_dan_and_tyler_isolation() -> None:
    """D-04-01: Bare 'Dan' or 'Tyler' must NOT merge into Drew Oakley / Tyler outside NEA/PartnerAdvisory context."""
    # Context-free call
    cid_dan, cname_dan = resolve_canonical_person("person:dan", "Dan")
    assert cid_dan == "person:dan"
    assert cname_dan == "Dan"

    # Non-NEA/PartnerAdvisory tenant document (e.g., Meridian Utility Corp)
    dominion_proj: Dict[str, Any] = {
        "project": "market_intelligence",
        "slug": "meridian_utility_Distribution",
        "evidence_index": {
            "ev-1": {"exact_text": "Dan and Tyler presented the Dominion grid reliability forecast."},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:dan", "type": "person", "name": "Dan"},
                {"id": "person:tyler", "type": "person", "name": "Tyler"},
                {"id": "org:meridian-utility", "type": "org", "name": "Meridian Utility Corp"},
            ],
            "relationship_signals": [],
        },
    }
    canonicalize_projection_entities(dominion_proj, {"project": "market_intelligence"})
    upserted_ids = {e["id"] for e in dominion_proj["graph_mutations"]["entity_upserts"]}
    assert "person:dan-okray" not in upserted_ids
    assert "person:dan" in upserted_ids

    # NEA/PartnerAdvisory tenant document DOES resolve bare Dan -> Drew Oakley
    nea_proj: Dict[str, Any] = {
        "project": "nea",
        "slug": "Apex Global_Analytics_Planning",
        "evidence_index": {
            "ev-1": {"exact_text": "Dan reviewed the Apex Global Analytics budget with PartnerAdvisory."},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:dan", "type": "person", "name": "Dan"},
                {"id": "org:nea", "type": "org", "name": "NEA"},
                {"id": "org:partner_advisory-consulting", "type": "org", "name": "PartnerAdvisory Consulting"},
            ],
            "relationship_signals": [],
        },
    }
    canonicalize_projection_entities(nea_proj, {"project": "nea"})
    nea_ids = {e["id"]: e["name"] for e in nea_proj["graph_mutations"]["entity_upserts"]}
    assert nea_ids.get("person:dan-okray") == "Drew Oakley"
    assert nea_ids.get("org:apex_global-analytics") == "Apex Global Analytics"
    assert nea_ids.get("org:partner_advisory") == "PartnerAdvisory"


def test_d04_02_fuzzy_person_false_positive_prevention_and_3token_names() -> None:
    """D-04-02: Prevent 'D. Chan', 'J. Chen', 'Roy Lauffer' false merges while supporting 3-token middle initials."""
    # Single-initial with different surname ('Chan' vs 'Chen') must NOT merge into Diana Chen
    cid_chan, _ = resolve_canonical_person("person:d-chan", "D. Chan")
    assert cid_chan == "person:d-chan"

    #Different initial ('J. Chen') must NOT merge into Diana Chen
    cid_jchen, _ = resolve_canonical_person("person:j-chen", "J. Chen")
    assert cid_jchen == "person:j-chen"

    # Short 3-char first name 'Roy' must NOT fuzzy-merge into 'Ray' (Ray Lauffer)
    cid_roy, _ = resolve_canonical_person("person:roy-lauffer", "Roy Lauffer")
    assert cid_roy == "person:roy-lauffer"

    # Valid 3+ token names with middle initials MUST resolve to canonical person
    cid_matt, cname_matt = resolve_canonical_person("person:matthew-j-barrows", "Matthew J. Barrows")
    assert (cid_matt, cname_matt) == ("person:matthew-barrows", "Matthew Barrows")

    cid_chris, cname_chris = resolve_canonical_person("person:chris-r-nunalee", "Chris R. Nunalee")
    assert (cid_chris, cname_chris) == ("person:chris-nunalee", "Chris Nunalee")


def test_d04_03_and_d04_04_secondary_mutation_remapping_and_alias_preservation() -> None:
    """D-04-03 & D-04-04: Remap entity IDs across all secondary collections, preserve highest confidence/CONFIRMED, and retain aliases."""
    proj: Dict[str, Any] = {
        "project": "nea",
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:matt-barrows",
                    "type": "person",
                    "name": "Matt Barrows",
                    "attributes": {"aliases": ["M. Barrows (VP)"], "role": "VP"},
                    "evidence_ids": ["ev-1"],
                },
                {
                    "id": "person:matthew-barrows",
                    "type": "person",
                    "name": "Matthew Barrows",
                    "attributes": {"aliases": ["Matt B."], "department": "Engineering"},
                    "evidence_ids": ["ev-2"],
                },
                {
                    "id": "org:apex_global-analytics",
                    "type": "org",
                    "name": "Apex Global Analytics",
                    "attributes": {"aliases": ["NEA"]},
                    "evidence_ids": ["ev-1"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-1",
                    "from_id": "person:matt-barrows",
                    "to_id": "org:nea",
                    "predicate": "MEMBER_OF",
                    "confidence": 0.65,
                    "epistemic_status": "PROPOSED",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev-1"],
                },
                {
                    "signal_id": "sig-2",
                    "from_id": "person:matthew-barrows",
                    "to_id": "org:apex_global-analytics",
                    "predicate": "MEMBER_OF",
                    "confidence": 0.92,
                    "epistemic_status": "CONFIRMED",
                    "mutation": "CONFIRM",
                    "evidence_ids": ["ev-2"],
                },
            ],
            "decision_updates": [{"id": "dec-1", "owner_id": "person:matt-barrows", "stakeholders": ["person:matt-barrows", "org:nea"]}],
            "proposal_updates": [{"id": "prop-1", "actor_id": "person:matt-barrows"}],
            "risk_updates": [{"id": "risk-1", "owner_id": "person:matt-barrows", "affected_entities": ["org:nea"]}],
            "action_updates": [{"id": "act-1", "assigned_to": "person:matt-barrows"}],
            "strategic_intent_signals": [{"id": "si-1", "subject_id": "org:nea", "entity_ids": ["person:matt-barrows"]}],
        },
    }
    canonicalize_projection_entities(proj)
    gm = proj["graph_mutations"]

    # Entity alias & attribute preservation
    mb = next(e for e in gm["entity_upserts"] if e["id"] == "person:matthew-barrows")
    aliases = mb["attributes"]["aliases"]
    assert "Matt Barrows" in aliases
    assert "M. Barrows (VP)" in aliases
    assert "Matt B." in aliases
    assert mb["attributes"]["role"] == "VP"
    assert mb["attributes"]["department"] == "Engineering"

    nea_ent = next(e for e in gm["entity_upserts"] if e["id"] == "org:apex_global-analytics")
    assert "NEA" in nea_ent["attributes"]["aliases"]

    # Colliding relationship merge preserves max confidence and CONFIRMED/CONFIRM
    assert len(gm["relationship_signals"]) == 1
    rel = gm["relationship_signals"][0]
    assert rel["from_id"] == "person:matthew-barrows"
    assert rel["to_id"] == "org:apex_global-analytics"
    assert rel["confidence"] == 0.92
    assert rel["epistemic_status"] == "CONFIRMED"
    assert rel["mutation"] == "CONFIRM"
    assert set(rel["evidence_ids"]) == {"ev-1", "ev-2"}

    # Secondary collections remapped
    assert gm["decision_updates"][0]["owner_id"] == "person:matthew-barrows"
    assert gm["decision_updates"][0]["stakeholders"] == ["person:matthew-barrows", "org:apex_global-analytics"]
    assert gm["proposal_updates"][0]["actor_id"] == "person:matthew-barrows"
    assert gm["risk_updates"][0]["owner_id"] == "person:matthew-barrows"
    assert gm["risk_updates"][0]["affected_entities"] == ["org:apex_global-analytics"]
    assert gm["action_updates"][0]["assigned_to"] == "person:matthew-barrows"
    assert gm["strategic_intent_signals"][0]["subject_id"] == "org:apex_global-analytics"
    assert gm["strategic_intent_signals"][0]["entity_ids"] == ["person:matthew-barrows"]


def test_d04_05_and_d04_06_healer_corroboration_28char_prefix_and_c16_external_degree() -> None:
    """D-04-05 & D-04-06: Uncorroborated auto-edges get PROPOSED + auto_healed=True; 28-char prefix collisions prevented; external C-16 degree counted."""
    proj: Dict[str, Any] = {
        "context_pack": {
            "known_entities": [{"id": "org:external-parent-holding", "name": "External Parent Holding"}],
        },
        "evidence_index": {
            "ev-anchor": {"exact_text": "Project Alpha is the core platform."},
            "ev-ws1": {"exact_text": "Infrastructure Automation Phase 1 handles compute."},
            "ev-ws2": {"exact_text": "Project Alpha and Infrastructure Automation Phase 2 operate together in production."},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "project:alpha", "type": "project", "name": "Project Alpha", "evidence_ids": ["ev-anchor"]},
                {
                    "id": "workstream:infrastructure-automation-phase-1",
                    "type": "workstream",
                    "name": "Infrastructure Automation Phase 1",
                    "evidence_ids": ["ev-ws1"],
                },
                {
                    "id": "workstream:infrastructure-automation-phase-2",
                    "type": "workstream",
                    "name": "Infrastructure Automation Phase 2",
                    "evidence_ids": ["ev-ws2"],
                },
                {
                    "id": "person:connected-to-external",
                    "type": "person",
                    "name": "External Liaison",
                    "evidence_ids": ["ev-anchor"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-ext-1",
                    "from_id": "person:connected-to-external",
                    "to_id": "org:external-parent-holding",
                    "predicate": "MEMBER_OF",
                    "evidence_ids": ["ev-anchor"],
                }
            ],
        },
    }
    changes = heal_disconnected_document_entities(proj, {"project": "alpha"})
    # person:connected-to-external already has degree 1 via C-16 known_entities org:external-parent-holding
    assert not any(c.startswith("Connected orphan 'person:connected-to-external'") for c in changes)

    rels = proj["graph_mutations"]["relationship_signals"]
    auto_rels = [
        r for r in rels
        if r.get("auto_healed") is True and ("phase-1" in r["to_id"] or "phase-2" in r["to_id"])
    ]
    assert len(auto_rels) == 2

    # 28-char prefix collision prevented via 8-char SHA256 digest suffix
    sig_ids = {r["signal_id"] for r in auto_rels}
    assert len(sig_ids) == 2

    # Phase 1 does NOT co-occur with Project Alpha -> PROPOSED / CREATE
    rel_p1 = next(r for r in auto_rels if "phase-1" in r["to_id"] or "phase-1" in r["from_id"])
    assert rel_p1["epistemic_status"] == "PROPOSED"
    assert rel_p1["mutation"] == "CREATE"
    assert rel_p1["evidence_ids"] == ["ev-ws1"]

    # Phase 2 DOES co-occur with Project Alpha in ev-ws2 -> CONFIRMED / CONFIRM
    rel_p2 = next(r for r in auto_rels if "phase-2" in r["to_id"] or "phase-2" in r["from_id"])
    assert rel_p2["epistemic_status"] == "CONFIRMED"
    assert rel_p2["mutation"] == "CONFIRM"
    assert rel_p2["evidence_ids"] == ["ev-ws2"]


def test_d04_07_audit_dedup_context_isolation_and_file_stat_invalidation() -> None:
    """D-04-07: audit_dedup caches isolate stage_context/project, invalidate on in-place file modification, and accumulate elapsed_saved_sec."""
    from unittest.mock import patch

    reset_healing_counters()
    reset_validation_counters()
    calls = 0

    def fake_heal(f3_input: Any, metadata: Any = None, stage_context: str = "document_pipeline") -> tuple[str, list[str]]:
        nonlocal calls
        calls += 1
        return f"healed:{calls}:{f3_input}", [f"change-{calls}"]

    with patch("dual_pipeline.transcript_qa_core.healing_mesh.DeterministicHealer.heal_output_3", side_effect=fake_heal):
        res_a1, _ = healed_output_3_cached('{"x": 1}', metadata={"project": "proj_a"}, stage_context="stage_a")
        res_b1, _ = healed_output_3_cached('{"x": 1}', metadata={"project": "proj_b"}, stage_context="stage_b")
        assert calls == 2
        assert res_a1 != res_b1

        # Cache hit on identical context increments cache_hits and elapsed_saved_sec
        res_a2, _ = healed_output_3_cached('{"x": 1}', metadata={"project": "proj_a"}, stage_context="stage_a")
        assert res_a2 == res_a1
        hc = get_healing_counters()
        assert hc["cache_hits"] == 1
        assert hc["elapsed_saved_sec"] > 0.0

    # Pair validation cache invalidates when underlying file size/mtime_ns changes in-place
    with tempfile.TemporaryDirectory() as tmpdir:
        gpath = Path(tmpdir) / "graph.sqlite"
        spath = Path(tmpdir) / "sidecar.sqlite"
        gpath.write_bytes(b"v1")
        spath.write_bytes(b"s1")
        pair = {
            "graph_path": str(gpath),
            "graph_sha256": "stale_manifest_sha",
            "sidecar_path": str(spath),
            "sidecar_sha256": "stale_sidecar_sha",
            "generation_id": "gen-1",
        }
        vcalls = 0

        class FakeManager:
            def _resolve(self, p: str) -> Path:
                return Path(p)

            def _uncached_validate_pair(self, p: Dict[str, Any]) -> Dict[str, Any]:
                nonlocal vcalls
                vcalls += 1
                return {"call": vcalls, "bytes": gpath.read_bytes().decode()}

        mgr = FakeManager()
        v1 = validate_pair_cached(mgr, pair)
        v2 = validate_pair_cached(mgr, pair)
        assert v1 == v2
        assert vcalls == 1
        vc = get_validation_counters()
        assert vc["cache_hits"] == 1
        assert vc["elapsed_saved_sec"] > 0.0

        # Mutate file in-place without changing pair dict
        gpath.write_bytes(b"v2-mutated-on-disk")
        v3 = validate_pair_cached(mgr, pair)
        assert vcalls == 2
        assert v3["bytes"] == "v2-mutated-on-disk"


def test_d04_08_to_d04_11_assertion_dedup_transitive_chain_hitl_and_paraphrase() -> None:
    """D-04-08..D-04-11: Prevent transitive supersession chains, skip hitl_required by default, transfer evidence, and detect paraphrases."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            claim_text TEXT,
            confidence_score REAL,
            source_authority_rank INTEGER,
            primary_source_id TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            recorded_at INTEGER,
            created_at INTEGER,
            superseded_at TEXT,
            superseded_by_assertion_id TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT,
            created_at TEXT,
            PRIMARY KEY (assertion_id, evidence_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE audit_event (
            audit_event_id TEXT PRIMARY KEY,
            actor_or_process TEXT,
            action TEXT,
            record_type TEXT,
            record_id TEXT,
            new_value_json TEXT,
            event_timestamp TEXT,
            reason TEXT
        )"""
    )

    # 3-way cluster with active/passive word-order paraphrase and distinct confidence scores (delta >= 0.20 -> hitl_required=False)
    conn.executemany(
        """INSERT INTO assertion VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', 1, ?, ?, NULL, NULL)""",
        [
            ("ast-1", "org:partner_advisory", "DELIVERS", "project:nexus", None, "PartnerAdvisory delivers the DocIQ platform for Apex Global", 0.50, 1, "src-1", 100, 100),
            ("ast-2", "org:partner_advisory", "DELIVERS", "project:nexus", None, "The DocIQ platform for Apex Global is delivered by PartnerAdvisory", 0.72, 2, "src-2", 200, 200),
            ("ast-3", "org:partner_advisory", "DELIVERS", "project:nexus", None, "PartnerAdvisory delivers DocIQ platform for Apex Global", 0.95, 3, "src-3", 300, 300),
            # Equal-confidence pair -> hitl_required=True
            ("ast-hitl-1", "org:nea", "USES", "system:forge", None, "NEA uses FORGE optimization engine", 0.80, 1, "src-1", 100, 100),
            ("ast-hitl-2", "org:nea", "USES", "system:forge", None, "NEA uses the FORGE optimization engine", 0.80, 1, "src-2", 200, 200),
        ],
    )
    conn.executemany(
        "INSERT INTO assertion_evidence VALUES (?, ?, 'supports', '2026-10-25T00:00:00Z')",
        [("ast-1", "ev-101"), ("ast-2", "ev-102"), ("ast-3", "ev-103")],
    )
    conn.commit()

    proposals = find_semantic_duplicates(conn, similarity_threshold=0.80)
    # Verify no loser is ever a winner (zero transitive chains)
    winners = {p["winner_id"] for p in proposals}
    losers = {p["loser_id"] for p in proposals}
    assert winners.isdisjoint(losers), f"Transitive chain detected: winners={winners} & losers={losers}"
    assert "ast-3" in winners
    assert {"ast-1", "ast-2"}.issubset(losers)

    # Default apply_supersessions skips hitl_required=True
    applied_default = apply_supersessions(conn, proposals)
    assert applied_default == 2

    # Verify ast-hitl-1 was NOT superseded because hitl_required=True
    hitl_row = conn.execute("SELECT currentness_state FROM assertion WHERE assertion_id = 'ast-hitl-1'").fetchone()
    assert hitl_row[0] == "current"

    # Verify evidence from ast-1 and ast-2 transferred to ast-3
    ev_ast3 = {
        r[0] for r in conn.execute("SELECT evidence_id FROM assertion_evidence WHERE assertion_id = 'ast-3'").fetchall()
    }
    assert ev_ast3 == {"ev-101", "ev-102", "ev-103"}

    # Verify audit_event logged
    audit_cnt = conn.execute("SELECT count(*) FROM audit_event WHERE action = 'supersede_duplicates'").fetchone()[0]
    assert audit_cnt == 1


def test_d04_12_evidence_dedup_page_locator_distinct_quotes_and_inband_dedup() -> None:
    """D-04-12: Distinct quotes on page:1 are NOT flagged as locator_duplicates; identical (locator, text) are merged in-band."""
    proj: Dict[str, Any] = {
        "evidence_index": {
            "ev-1": {"locator": "page:1", "exact_text": "Revenue grew 14% year over year in Q3."},
            "ev-2": {"locator": "page:1", "exact_text": "Staffing transition completes on November 15."},
            "ev-3": {"locator": "page:1", "exact_text": "Revenue grew 14% year over year in Q3."},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "project:sample", "type": "project", "name": "Sample", "evidence_ids": ["ev-1", "ev-3"]},
            ],
            "relationship_signals": [
                {"from_id": "project:sample", "to_id": "org:nea", "predicate": "PART_OF", "evidence_ids": ["ev-3"]},
            ],
        },
    }
    # In-band deduplication collapses ev-3 into ev-1 and rewrites pointers
    deduplicate_projection_evidence_and_signals(proj)
    assert set(proj["evidence_index"].keys()) == {"ev-1", "ev-2"}
    assert proj["graph_mutations"]["entity_upserts"][0]["evidence_ids"] == ["ev-1"]
    assert proj["graph_mutations"]["relationship_signals"][0]["evidence_ids"] == ["ev-1"]

    # Verify tier2_scan4_evidence_dedup does NOT flag distinct quotes sharing page:1 as locator_duplicates
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            evidence_fingerprint TEXT,
            source_id TEXT,
            source_version_id TEXT,
            locator_kind TEXT,
            locator_value TEXT,
            page_number INTEGER,
            section_name TEXT,
            paragraph_number INTEGER,
            transcript_timestamp TEXT,
            direct_quote TEXT,
            evidence_directness TEXT,
            source_authority_rank INTEGER,
            extraction_confidence REAL,
            corroboration_group TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT,
            independent_corroboration INTEGER
        )"""
    )
    conn.executemany(
        """INSERT INTO evidence_item VALUES (?, ?, 'src-doc-1', 'ver-1', 'page', 'page:1', 1, NULL, NULL, NULL, ?, 'direct', 1, 0.9, NULL)""",
        [
            ("ev-1", "fp-1", "Revenue grew 14% year over year in Q3."),
            ("ev-2", "fp-2", "Staffing transition completes on November 15."),
        ],
    )
    conn.commit()
    report = scan_evidence_fragmentation(conn)
    assert report["locator_duplicates"] == []
    assert report["total_duplicate_pairs"] == 0
