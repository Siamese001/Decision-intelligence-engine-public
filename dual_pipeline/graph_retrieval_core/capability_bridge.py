"""Cross-workstream technical alias normalization and capability-domain bridging.

Unifies split-identity cloud/technical anchors (such as Amazon S3 vs AWS S3)
and connects isolated technical anchors across workstreams (such as Percepto,
Skydio Drone, Buzz Solutions, Vision Model, and Amazon S3) via deterministic
capability-domain virtual edges.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .constants import SPLIT_IDENTITY_RELATIONSHIP

SHARED_CAPABILITY_RELATIONSHIP = "SHARES_CAPABILITY_DOMAIN"
_TECH_NODE_TYPES = frozenset({"technical_anchor", "system", "deliverable", "pattern"})
_BRIDGEABLE_NODE_TYPES = frozenset(
    {
        "technical_anchor",
        "system",
        "deliverable",
        "pattern",
        "workstream",
        "program",
        "sow",
        "project",
        "org",
        "person",
        "stakeholder",
        "entity",
    }
)

_CANONICAL_TECH_ALIASES: dict[str, str] = {
    "amazon s3": "cloud_s3_storage",
    "aws s3": "cloud_s3_storage",
    "s3 ingestion bucket": "cloud_s3_storage",
    "lens s3 ingestion pipeline": "cloud_s3_storage",
    "aws s3 tags": "cloud_s3_storage",
    "unifier s3 api": "cloud_s3_storage",
    "pano ai": "pano_ai_cameras",
    "pano ai smoke detection cameras": "pano_ai_cameras",
    "pano camera alerts": "pano_ai_cameras",
    "pano 360": "pano_ai_cameras",
    "vision model": "computer_vision_model",
    "few shot vision prompting": "computer_vision_model",
    "c3 ai": "c3_ai_platform",
    "c3 ai inc": "c3_ai_platform",
    "c3 ai reliability": "c3_ai_platform",
}

CAPABILITY_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "aerial_drone_inspection": (
        "drone",
        "drones",
        "percepto",
        "skydio",
        "noteworthy",
        "digital surveyor",
        "mobile mapping lidar",
        "aerial",
    ),
    "computer_vision_image_analytics": (
        "computer vision",
        "computervision",
        "vision model",
        "few shot vision",
        "image analysis",
        "damage severity",
        "buzz solutions",
        "noteworthy",
        "smoke detection camera",
        "pano ai",
        "nameplate image",
        "inspection imagery",
    ),
    "cloud_object_storage_ingestion": (
        "amazon s3",
        "aws s3",
        "s3 ingestion",
        "s3 bucket",
        "s3 tag",
        "unifier s3",
        "inspection imagery",
    ),
    "transmission_sensing_telemetry": (
        "linevision",
        "heimdall power",
        "prisma photonics",
        "dynamic ratings",
        "technosylva",
        "technosilva",
    ),
    "operating_model_pod_topology": (
        "segregated pods",
        "shared domain pods",
        "forward deployed teams",
        "accelerate power",
        "accelerated power",
        "pod model",
        "full stack engineers per workstream",
        "pineapple",
        "geographic pods",
    ),
    "talent_screening_and_churn": (
        "screening contractor",
        "qualification bar",
        "anti patterns",
        "re screening",
        "screening velocity",
        "7 years of experience",
        "turnovers",
        "single threaded",
    ),
    "commercial_sow_economics": (
        "gain sharing",
        "loss leader",
        "45 higher",
        "42 hours a week",
        "six hundred thousand",
    ),
    "facility_badging_and_it_access": (
        "fieldglass",
        "dedicated seating",
        "3 days per week",
        "three days per week",
        "deprovisioning",
        "two weeks prior",
        "terminates system access",
    ),
}

CAPABILITY_PIPELINE_BRIDGES: tuple[tuple[str, str], ...] = (
    ("aerial_drone_inspection", "computer_vision_image_analytics"),
    ("computer_vision_image_analytics", "cloud_object_storage_ingestion"),
    ("aerial_drone_inspection", "cloud_object_storage_ingestion"),
    ("operating_model_pod_topology", "talent_screening_and_churn"),
    ("operating_model_pod_topology", "commercial_sow_economics"),
    ("operating_model_pod_topology", "facility_badging_and_it_access"),
    ("talent_screening_and_churn", "commercial_sow_economics"),
)


def normalize_entity_text(value: str) -> str:
    """Normalize text into lowercase alphanumeric space-separated tokens."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _phonetic_surname_key(token: str) -> str:
    t = re.sub(r"[^a-z]", "", token.lower())
    if not t:
        return ""
    for pat, repl in ((r"ei", "i"), (r"ie", "i"), (r"ce$", "s"), (r"se$", "s"), (r"ss$", "s"), (r"(.)\1+", r"\1")):
        t = re.sub(pat, repl, t)
    return t


def _pairs_from_group(group: list[str]) -> set[tuple[str, str]]:
    if not (1 < len(group) <= 6):
        return set()
    sorted_ids = sorted(set(group))
    return {
        (first_id, second_id)
        for idx, first_id in enumerate(sorted_ids)
        for second_id in sorted_ids[idx + 1 :]
    }


def _collect_phonetic_person_twins(nodes: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
    person_types = {"person", "stakeholder", "entity"}
    by_first_and_phonetic_last: dict[tuple[str, str], list[str]] = defaultdict(list)
    for node_id, node in nodes.items():
        if node.get("node_type") not in person_types:
            continue
        words = normalize_entity_text(node.get("canonical_name", "")).split()
        if len(words) == 2 and min(len(words[0]), len(words[1])) >= 3:
            p_last = _phonetic_surname_key(words[1])
            if len(p_last) >= 3:
                by_first_and_phonetic_last[(words[0], p_last)].append(node_id)
    pairs: set[tuple[str, str]] = set()
    for group in by_first_and_phonetic_last.values():
        pairs.update(_pairs_from_group(group))
    return pairs


def resolve_canonical_tech_alias(norm_name: str) -> str:
    """Return canonical equivalence key for known split-identity technical anchors."""
    if norm_name in _CANONICAL_TECH_ALIASES:
        return _CANONICAL_TECH_ALIASES[norm_name]
    for prefix in ("amazon ", "aws "):
        if norm_name.startswith(prefix) and len(norm_name) > len(prefix) + 1:
            stripped = norm_name[len(prefix) :].strip()
            if stripped == "s3" or stripped.startswith("s3 "):
                return "cloud_s3_storage"
    return ""


def collect_technical_alias_pairs(nodes: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
    """Pair split-identity technical anchors sharing a canonical technical alias."""
    by_alias: dict[str, list[str]] = defaultdict(list)
    for node_id, node in nodes.items():
        if node.get("node_type") not in _TECH_NODE_TYPES:
            continue
        norm_name = normalize_entity_text(node.get("canonical_name", ""))
        alias_key = resolve_canonical_tech_alias(norm_name)
        if alias_key:
            by_alias[alias_key].append(node_id)

    pairs: set[tuple[str, str]] = set()
    for group in by_alias.values():
        sorted_ids = sorted(set(group))
        for idx, first_id in enumerate(sorted_ids[:8]):
            for second_id in sorted_ids[idx + 1 : 8]:
                pairs.add((first_id, second_id))
    return pairs


def _collect_node_text_corpus(
    nodes: dict[str, dict[str, Any]],
    assertions: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Build combined normalized name and active claim text for each bridgeable node."""
    corpus: dict[str, list[str]] = {
        nid: [normalize_entity_text(node.get("canonical_name", ""))]
        for nid, node in nodes.items()
        if node.get("node_type") in _BRIDGEABLE_NODE_TYPES
    }
    for assn in assertions.values():
        if assn.get("currentness_state") != "current":
            continue
        subj_id = assn.get("subject_node_id")
        if subj_id in corpus and assn.get("object_literal"):
            corpus[subj_id].append(normalize_entity_text(str(assn["object_literal"])))
    return {nid: " ".join(parts) for nid, parts in corpus.items()}


def _classify_capability_domains(
    nodes: dict[str, dict[str, Any]],
    assertions: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Assign technical anchor nodes to deterministic capability domains."""
    node_corpus = _collect_node_text_corpus(nodes, assertions)
    domain_members: dict[str, list[str]] = defaultdict(list)
    for nid, text in sorted(node_corpus.items()):
        if not text:
            continue
        padded = f" {text} "
        for domain, keywords in CAPABILITY_DOMAIN_KEYWORDS.items():
            if any(f" {kw} " in padded for kw in keywords):
                domain_members[domain].append(nid)
    return domain_members


def _select_domain_hubs(member_ids: list[str], nodes: dict[str, dict[str, Any]], limit: int = 4) -> list[str]:
    """Select deterministic representative hub nodes for a capability domain."""
    return sorted(
        set(member_ids),
        key=lambda nid: (
            0 if nodes.get(nid, {}).get("node_type") == "technical_anchor" else 1,
            normalize_entity_text(nodes.get(nid, {}).get("canonical_name", "")),
            nid,
        ),
    )[:limit]


def _connect_member_to_hubs(member_id: str, hubs: list[str]) -> set[tuple[str, str]]:
    return {
        (min(member_id, hub_id), max(member_id, hub_id))
        for hub_id in hubs
        if member_id != hub_id
    }


def _connect_hub_groups(left_hubs: list[str], right_hubs: list[str]) -> set[tuple[str, str]]:
    return {
        (min(left_hub, right_hub), max(left_hub, right_hub))
        for left_hub in left_hubs
        for right_hub in right_hubs
        if left_hub != right_hub
    }


def collect_capability_bridge_pairs(
    nodes: dict[str, dict[str, Any]],
    assertions: dict[str, dict[str, Any]],
) -> set[tuple[str, str]]:
    """Compute intra-domain and cross-domain capability bridge node pairs."""
    domain_members = _classify_capability_domains(nodes, assertions)
    domain_hubs = {
        domain: _select_domain_hubs(members, nodes)
        for domain, members in domain_members.items()
        if members
    }
    pairs: set[tuple[str, str]] = set()
    for domain, members in domain_members.items():
        hubs = domain_hubs.get(domain, [])
        for member_id in members:
            pairs.update(_connect_member_to_hubs(member_id, hubs))

    for left_domain, right_domain in CAPABILITY_PIPELINE_BRIDGES:
        pairs.update(
            _connect_hub_groups(
                domain_hubs.get(left_domain, []),
                domain_hubs.get(right_domain, []),
            )
        )
    return pairs


def resolve_bridge_edge_type(
    u_id: str,
    v_id: str,
    nodes: dict[str, dict[str, Any]],
    assertions: dict[str, dict[str, Any]],
) -> str:
    """Resolve a directional causal or counterfactual edge_type for a capability bridge."""
    corpus = _collect_node_text_corpus(
        {k: nodes[k] for k in (u_id, v_id) if k in nodes},
        assertions,
    )
    target_text = corpus.get(v_id, "")
    combined = f"{corpus.get(u_id, '')} {target_text}"
    if any(tok in target_text for tok in ("phasing out", "segregated pods", "engineers per workstream", "1 8", "one to eight")):
        return "CONSTRAINS_CAPACITY_OF"
    if any(tok in target_text for tok in ("qualification bar", "pass rate", "deprovisioning", "two weeks prior", "3 days per week", "three days per week")):
        return "GOVERNED_BY_STANDARD"
    if any(tok in combined for tok in ("45 higher", "42 hours a week", "six hundred thousand", "outcome based", "gain sharing")):
        return "COUNTERFACTUAL_TO"
    return SHARED_CAPABILITY_RELATIONSHIP


def inject_capability_bridge_edges(
    nodes: dict[str, dict[str, Any]],
    assertions: dict[str, dict[str, Any]],
    node_primary_assertion: dict[str, str],
    supporting_evidence: dict[str, list[str]],
    adjacency: dict[str, list[dict[str, Any]]],
    seen_projection: set[tuple[str, str, str]],
    append_edge_fn: Any,
) -> None:
    """Inject bidirectional capability-domain virtual edges into traversal adjacency."""
    for first_id, second_id in sorted(collect_capability_bridge_pairs(nodes, assertions)):
        for u_id, v_id in ((first_id, second_id), (second_id, first_id)):
            bridge_aid = (
                node_primary_assertion.get(v_id)
                or node_primary_assertion.get(u_id)
                or f"capability-bridge:{u_id}:{v_id}"
            )
            edge_type = resolve_bridge_edge_type(u_id, v_id, nodes, assertions)
            append_edge_fn(
                u_id,
                v_id,
                bridge_aid,
                SHARED_CAPABILITY_RELATIONSHIP,
                edge_type,
                "high",
                0.92,
                nodes,
                assertions.get(bridge_aid, {}),
                supporting_evidence,
                adjacency,
                seen_projection,
            )


def collect_csr_virtual_edge_pairs(connection: Any) -> set[tuple[str, str]]:
    """Collect split-identity, technical-alias, and capability-bridge edges for CSR compilation."""
    has_node = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='node'"
    ).fetchone()
    has_assertion = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion'"
    ).fetchone()
    if not has_node or not has_assertion:
        return set()

    nodes = {
        row["node_id"]: dict(row)
        for row in connection.execute(
            "SELECT node_id, canonical_name, node_type FROM node WHERE active=1 ORDER BY node_id"
        )
    }
    assertions = {
        row["assertion_id"]: dict(row)
        for row in connection.execute(
            "SELECT assertion_id, subject_node_id, object_node_id, object_literal, currentness_state "
            "FROM assertion WHERE currentness_state='current'"
        )
    }
    return (
        collect_technical_alias_pairs(nodes)
        | collect_capability_bridge_pairs(nodes, assertions)
    )


__all__ = [
    "CAPABILITY_DOMAIN_KEYWORDS",
    "CAPABILITY_PIPELINE_BRIDGES",
    "SHARED_CAPABILITY_RELATIONSHIP",
    "SPLIT_IDENTITY_RELATIONSHIP",
    "collect_capability_bridge_pairs",
    "collect_csr_virtual_edge_pairs",
    "collect_technical_alias_pairs",
    "inject_capability_bridge_edges",
    "normalize_entity_text",
    "resolve_bridge_edge_type",
    "resolve_canonical_tech_alias",
]
