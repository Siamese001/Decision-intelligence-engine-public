"""Adversarial & edge-case tests for Wave 2 role predicate resolution, duplicate role ambiguity, and affiliation mutex."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.graph_signal_core.mutex_invalidation import (
    find_conflicting_assertions,
)
from dual_pipeline.identity_core.catalog import GraphEntityCatalog
from dual_pipeline.identity_core.records import EntityRecord
from dual_pipeline.identity_core.resolver import resolve_mention
from dual_pipeline.intake_core.change_set_helpers import resolve_relationship_predicate


def test_boundary_empty_and_missing_predicate_fields() -> None:
    """Category 1 (Boundary payload): Empty candidate dict and missing edge_traits."""
    assert resolve_relationship_predicate({}, "missing-id", {}) == "RELATED_TO"
    assert resolve_relationship_predicate({"normalized_text": ""}, "id", {"id": {}}) == "RELATED_TO"


def test_malformed_edge_traits_and_alias_mapping() -> None:
    """Category 2 (Malformed input): Non-string predicate traits and all alias mappings."""
    cand = {"normalized_text": "Brandon Que HAS_ROLE Solution Architect Lead."}
    assert resolve_relationship_predicate(cand, "c1", {"c1": {"relationship_predicate": None}}) == "HAS_ROLE_IN"
    assert resolve_relationship_predicate({"normalized_text": "Egle Tamasevice MEMBER_OF Apex Global Enterprises."}, "c2", {}) == "AFFILIATED_WITH"
    assert resolve_relationship_predicate({"normalized_text": "Jeffrey Glade OWNS Project Beacon."}, "c3", {}) == "OWNS_OR_LEADS"


def test_duplicate_role_node_disambiguation_without_quarantine() -> None:
    """Category 5 & 7 (Invariant bypass / Exact production regression): Duplicate role:tech-lead nodes resolve deterministically."""
    catalog = GraphEntityCatalog.__new__(GraphEntityCatalog)
    r_cand = EntityRecord(
        entity_id="entity-dup-candidate",
        node_type="role_group",
        canonical_name="Tech Lead",
        normalized_name="techlead",
        description="Candidate role",
        status="candidate",
    )
    r_canon = EntityRecord(
        entity_id="entity-69ef9733972222d01c2fcb3af0ce",
        node_type="role_group",
        canonical_name="Tech Lead",
        normalized_name="techlead",
        description="Canonical role",
        status="canonical",
    )
    catalog.entities = {
        "entity-dup-candidate": r_cand,
        "entity-69ef9733972222d01c2fcb3af0ce": r_canon,
    }
    catalog.names = {"techlead": {"entity-dup-candidate", "entity-69ef9733972222d01c2fcb3af0ce"}}
    catalog.aliases = {}
    catalog.entity_ids_by_normalized_id = {}

    bundle = {
        "run_id": "run-intake-0123456789abcdef01234567",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "source_id": "source-c8ba9093a5a3687e0d016e35",
        "source_version_id": "source-version-6672b73cedffb52624aecaff",
        "classification": "internal",
        "audiences": ["internal"],
        "extensions": {"com.partner_advisory.nee.context": {"attendees": ["Egle Tamasevice"], "workstream": "project_beacon"}},
    }
    mention = {
        "mention_id": "mention-0123456789abcdef01234567",
        "reported_text": "Tech Lead",
        "stable_identifier": "role:tech-lead",
        "context": "Egle Tamasevice HAS_ROLE Tech Lead.",
    }
    profile = {
        "node_type": "role_group",
        "entity_intent": "resolve_or_create",
        "mutation_intent": "CONFIRM",
        "declaration_status": "DECLARED",
        "stable_identifier": "role:tech-lead",
        "source_profile_digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    }
    res = resolve_mention(
        catalog,
        bundle,
        mention,
        intent_profile=profile,
        valid_node_types=["role_group", "stakeholder"],
    )
    assert res["outcome"] == "AUTO_LINK_EXACT"
    assert res["selected_entity_id"] == "entity-69ef9733972222d01c2fcb3af0ce"


def test_cross_org_mutex_isolation_regression(tmp_path: Path) -> None:
    """Category 6 & 7 (Rollback/recovery & Production regression): Client NEE vs PartnerAdvisory OWNS_OR_LEADS isolation."""
    db_path = tmp_path / "mutex_reg.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (node_id TEXT PRIMARY KEY, description TEXT);
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            scope_text TEXT,
            qualifiers_json TEXT,
            recorded_at TEXT,
            created_at TEXT,
            canonical_status TEXT,
            currentness_state TEXT
        );
        INSERT INTO node VALUES ('entity-egle', 'Tech Lead affiliated with Apex Global Enterprises.');
        INSERT INTO node VALUES ('person_andy_k', 'Delivery Lead at PartnerAdvisory.');
        INSERT INTO assertion VALUES (
            'a-egle', 'entity-egle', 'OWNS_OR_LEADS', 'workstream_project_beacon',
            'Egle Tamasevice Tech Lead', '{"affiliation": "Apex Global Enterprises"}',
            '2026-10-16', '2026-10-16', 'canonical', 'current'
        );
        INSERT INTO assertion VALUES (
            'a-andy', 'person_andy_k', 'OWNS_OR_LEADS', 'workstream_project_beacon',
            'Andy Kilinskis PartnerAdvisory Lead', '{"affiliation": "PartnerAdvisory"}',
            '2026-10-16', '2026-10-16', 'canonical', 'current'
        );
        """
    )
    conflicts = find_conflicting_assertions(
        conn,
        object_node_id="workstream_project_beacon",
        predicate="OWNS_OR_LEADS",
        incoming_assertion_id="a-egle",
        subject_node_id="entity-egle",
    )
    conn.close()
    assert conflicts == []
