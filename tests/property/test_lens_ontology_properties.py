"""
tests/property/test_lens_ontology_properties.py

Hypothesis property suite verifying 18-role taxonomy categorization idempotence,
multi-label conflict resolution, and boundary invariance in
dual_pipeline/transcript_qa_core/lens_ontology.py.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

from hypothesis import HealthCheck, given, settings, strategies as st

from dual_pipeline.transcript_qa_core.lens_ontology import (
    connect_conduit_and_api_gateway,
    connect_maximo_edge,
    heal_lens_core_systems,
    reground_single_lens_entity,
)

EIGHTEEN_ROLE_TAXONOMY: List[str] = [
    "role:account-lead",
    "role:delivery-lead",
    "role:platform-architect",
    "role:solution-architect",
    "role:data-architect",
    "role:ml-engineer",
    "role:software-engineer",
    "role:devops-engineer",
    "role:product-manager",
    "role:product-owner",
    "role:solution-owner",
    "role:scrum-master",
    "role:agile-coach",
    "role:business-analyst",
    "role:qa-lead",
    "role:security-architect",
    "role:executive-sponsor",
    "role:domain-sme",
]


@settings(max_examples=55, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    role_id=st.sampled_from(EIGHTEEN_ROLE_TAXONOMY),
    has_atlas_token=st.booleans(),
    speaker_name=st.sampled_from(["harper Hoffman", "Eduardo Juan More", "Alex Sanchez", "Amit Ayer"]),
)
def test_property_18_role_taxonomy_categorization_idempotence_and_boundary_invariance(
    role_id: str, has_atlas_token: bool, speaker_name: str
) -> None:
    """Property 1: Role entities across the 18-role taxonomy preserve their 'role:' prefix and 'role' type without being reclassified into org/system."""
    effective_id = f"{role_id}-atlas" if has_atlas_token else role_id
    role_name = effective_id.split(":", 1)[-1].replace("-", " ").title()
    ent: Dict[str, Any] = {
        "id": effective_id,
        "type": "role",
        "name": role_name,
        "description": f"Functional role {role_name} assigned to {speaker_name}.",
        "evidence_ids": ["ev-01"],
    }
    evidence_index: Dict[str, Any] = {
        "ev-01": {
            "exact_text": f"{speaker_name} serves in the {role_name} capacity for Project LENS.",
            "text": f"{speaker_name} serves in the {role_name} capacity for Project LENS.",
            "speaker": speaker_name,
        }
    }
    changes1: List[str] = []
    reground_single_lens_entity(ent, evidence_index, changes1)
    after_first = copy.deepcopy(ent)

    changes2: List[str] = []
    reground_single_lens_entity(ent, evidence_index, changes2)

    assert ent == after_first
    assert ent["id"].startswith("role:")
    assert ent["type"] == "role"


@settings(max_examples=55, deadline=None)
@given(
    lens_entity_id=st.sampled_from([
        "system:lens",
        "system:ocr-vlm-extraction-engine",
        "system:structured-data-store",
        "system:maximo",
        "system:ibm-maximo",
        "workstream:sow-2-maximo-write-back",
        "system:lens-chat-rule-builder",
        "system:lens-business-rule-registry",
        "system:dynamodb",
        "system:lens-s3-ingestion-pipeline",
        "system:atlas",
        "system:lens-admin-dashboard",
        "system:lens-review-queue",
        "system:lens-audit-trail",
        "workstream:asset-hierarchy-migration",
        "system:aws-s3",
        "system:max-core-endpoint",
        "system:amazon-cloudwatch",
        "system:conduit",
    ]),
    include_nameplate_kw=st.booleans(),
)
def test_property_reground_single_lens_entity_idempotence(
    lens_entity_id: str, include_nameplate_kw: bool
) -> None:
    """Property 2: reground_single_lens_entity is idempotent across all Project LENS system, org, and workstream entities."""
    kw_text = "nameplate extraction using ocr and dynamodb table scan in cloudwatch" if include_nameplate_kw else "general pipeline discussion"
    evidence_index: Dict[str, Any] = {
        "ev-06": {
            "exact_text": f"Project LENS processes images: {kw_text}.",
            "text": f"Project LENS processes images: {kw_text}.",
            "speaker": "Alex Sanchez",
        },
        "ev-07": {
            "exact_text": "SOW 2 covering write-back to IBM Maximo is dependent on Max Core and asset hierarchy migration.",
            "text": "SOW 2 covering write-back to IBM Maximo is dependent on Max Core and asset hierarchy migration.",
            "speaker": "Eduardo Juan More",
        },
    }
    ent: Dict[str, Any] = {
        "id": lens_entity_id,
        "type": "system",
        "name": "Initial Name",
        "description": "Initial description.",
        "evidence_ids": ["ev-06"],
    }
    changes1: List[str] = []
    reground_single_lens_entity(ent, evidence_index, changes1)
    snapshot = copy.deepcopy(ent)

    changes2: List[str] = []
    reground_single_lens_entity(ent, evidence_index, changes2)

    assert ent == snapshot
    assert isinstance(ent.get("name"), str) and len(ent["name"]) > 0
    assert isinstance(ent.get("description"), str) and len(ent["description"]) > 0


@settings(max_examples=55, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    source_keywords=st.lists(
        st.sampled_from([
            "project lens",
            "harper Hoffman",
            "ibm maximo",
            "interim system of record",
            "s3 image ingestion pipeline",
            "atlas team",
            "three-pane validation ui",
            "admin dashboard",
            "review queue",
            "uploaded to aws s3",
            "conduit mobile application",
            "national",
            "asset hierarchy migration",
            "audit trail",
            "cloudwatch dashboards",
            "ocr confidence score",
            "dynamodb table scan",
            "unified chat",
        ]),
        min_size=1,
        max_size=6,
        unique=True,
    )
)
def test_property_heal_lens_core_systems_idempotence_and_no_duplicates(
    source_keywords: List[str],
) -> None:
    """Property 3: heal_lens_core_systems grounds entities without creating duplicate IDs upon repeated execution."""
    norm_src = " ".join(source_keywords).lower()
    evidence_index: Dict[str, Any] = {
        "ev-01": {
            "exact_text": norm_src,
            "text": norm_src,
            "speaker": "harper Hoffman",
        },
        "ev-07": {
            "exact_text": f"Technical architecture covers {norm_src}.",
            "text": f"Technical architecture covers {norm_src}.",
            "speaker": "Alex Sanchez",
        },
    }
    entities: List[Dict[str, Any]] = []
    changes1: List[str] = []
    heal_lens_core_systems(entities, evidence_index, changes1, norm_src)
    snapshot_entities = copy.deepcopy(entities)
    ids_once = [e["id"] for e in entities]
    assert len(ids_once) == len(set(ids_once)), f"Duplicate entity IDs created: {ids_once}"

    changes2: List[str] = []
    heal_lens_core_systems(entities, evidence_index, changes2, norm_src)
    assert entities == snapshot_entities


@settings(max_examples=55, deadline=None)
@given(
    is_sow1_out_of_scope=st.booleans(),
    has_max_core=st.booleans(),
    has_asset_hierarchy=st.booleans(),
    has_api_gateway=st.booleans(),
)
def test_property_maximo_and_conduit_edge_conflict_resolution_idempotence(
    is_sow1_out_of_scope: bool,
    has_max_core: bool,
    has_asset_hierarchy: bool,
    has_api_gateway: bool,
) -> None:
    """Property 4: connect_maximo_edge and connect_conduit_and_api_gateway resolve SOW 1 vs SOW 2 boundary conflicts idempotently."""
    scope_text = (
        "SOW 1 is out of scope for Maximo write-back; SOW 2 covering write-back is dependent on asset hierarchy migration and max core endpoint."
        if is_sow1_out_of_scope
        else "LENS integrates with IBM Maximo for asset records."
    )
    evidence_index: Dict[str, Any] = {
        "ev-07": {
            "exact_text": scope_text,
            "text": scope_text,
            "speaker": "Eduardo Juan More",
        }
    }
    entities: List[Dict[str, Any]] = [
        {"id": "system:lens", "type": "system", "name": "Project LENS"},
        {"id": "system:ibm-maximo", "type": "system", "name": "IBM Maximo"},
        {"id": "system:conduit", "type": "system", "name": "Conduit"},
    ]
    if has_max_core:
        entities.append({"id": "system:max-core-endpoint", "type": "system", "name": "Max Core Endpoint"})
    if has_asset_hierarchy:
        entities.append({"id": "workstream:asset-hierarchy-migration", "type": "workstream", "name": "Asset Hierarchy Migration"})
    if has_api_gateway:
        entities.append({"id": "system:api-gateway", "type": "system", "name": "API Gateway"})

    signals: List[Dict[str, Any]] = [
        {
            "signal_id": "rel-lens--maximo--INTEGRATES_WITH",
            "from_id": "system:lens",
            "to_id": "system:ibm-maximo",
            "predicate": "INTEGRATES_WITH",
            "relationship": "INTEGRATES_WITH",
            "evidence_ids": ["ev-07"],
        },
        {
            "signal_id": "rel-conduit--lens--INTEGRATES_WITH",
            "from_id": "system:conduit",
            "to_id": "system:lens",
            "predicate": "INTEGRATES_WITH",
            "relationship": "INTEGRATES_WITH",
            "evidence_ids": ["ev-03"],
            "epistemic_status": "CONFIRMED",
        },
    ]

    changes1: List[str] = []
    connect_maximo_edge(signals, entities, evidence_index, changes1)
    connect_conduit_and_api_gateway(signals, entities, evidence_index, changes1)

    snapshot_signals = copy.deepcopy(signals)
    snapshot_entities = copy.deepcopy(entities)

    changes2: List[str] = []
    connect_maximo_edge(signals, entities, evidence_index, changes2)
    connect_conduit_and_api_gateway(signals, entities, evidence_index, changes2)

    assert signals == snapshot_signals
    assert entities == snapshot_entities

    if is_sow1_out_of_scope:
        # Direct LENS -> Maximo signal must be pruned in SOW 1 scope and rerouted via workstream:sow-2-maximo-write-back
        assert not any(
            s.get("from_id") == "system:lens" and s.get("to_id") in ("system:ibm-maximo", "system:maximo")
            for s in signals
        )
        assert any(s.get("from_id") == "workstream:sow-2-maximo-write-back" for s in signals)
