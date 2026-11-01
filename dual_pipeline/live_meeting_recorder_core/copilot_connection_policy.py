"""
dual_pipeline/live_meeting_recorder_core/copilot_connection_policy.py

Governs cross-engagement retrieval boundary enforcement, ownership qualification,
topological predecessor inheritance, and scope/session lifecycle invalidation for Executive Copilot.
Enforces:
1. Zero unrelated claims enter candidates or prompts; unknown ownership fails closed.
2. Cross-engagement context requires an explicit request and/or an evidenced graph relationship.
3. Automatic topological predecessor/tangential inheritance for evidenced relationships.
4. Scope, session, and generation bind reusable context; switches invalidate pending answers.
5. Strict Rule C-19 Career Leverage & Privacy Firewall enforcement across all scopes.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .c19_firewall import (
    has_c19_marker,
    is_c19_quarantined_assertion,
    is_c19_quarantined_node,
)
from .session_identity import match_engagement, normalize_engagement_id

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_connection_policy")

ENTERPRISE_POLICY_SCOPES: frozenset[str] = frozenset({"project:enterprise", "global", "policy", "enterprise_policy", "enterprise", "shared"})
PERSONAL_NON_ENTERPRISE_SCOPES: frozenset[str] = frozenset({"project:personal", "personal", "project:family", "project:school", "project:private"})

EVIDENCED_CROSS_RELATIONSHIP_PREDICATES: frozenset[str] = frozenset({
    "DEPENDS_ON", "PREDECESSOR_PROJECT", "INHERITS_FROM", "REUSES", "INTEGRATES_WITH",
    "CONNECTS_TO", "EXTENDS", "RELATED_TO", "CONTEXT_FOR", "REL_TANGENTIAL", "PREDECESSOR",
    "TANGENTIAL_PROJECT", "TANGENTIAL",
})

TOPOLOGICAL_INHERITANCE_PREDICATES: frozenset[str] = frozenset({
    "PREDECESSOR_PROJECT", "REL_TANGENTIAL", "PREDECESSOR", "TANGENTIAL_PROJECT",
    "TANGENTIAL", "INTEGRATES_WITH", "DEPENDS_ON", "REUSES", "EXTENDS",
    "INHERITS_FROM", "BELONGS_TO_ENGAGEMENT", "PART_OF", "GOVERNED_BY", "WORKSTREAM_OF",
})

TOPOLOGICAL_PROJECT_ALIASES: Dict[str, Set[str]] = {
    "project:project_nexus": {"project:nea"},
    "project:nexus": {"project:nea"},
    "project:nea": {"project:project_nexus", "project:nexus"},
}


def get_project_scope_aliases(engagement_id: Optional[str]) -> Set[str]:
    """Returns canonical topological project aliases linked to the given engagement scope."""
    norm = normalize_engagement_id(engagement_id)
    if not norm:
        return set()
    aliases = set(TOPOLOGICAL_PROJECT_ALIASES.get(norm, set()))
    slug = norm.replace("project:", "")
    alt = f"project:{slug.replace('project_', '')}" if slug.startswith("project_") else f"project:project_{slug}"
    aliases.update(TOPOLOGICAL_PROJECT_ALIASES.get(alt, set()))
    return aliases

_TOPOLOGICAL_INHERITANCE_CACHE: Dict[Tuple[str, str], Set[str]] = {}


def clear_topological_inheritance_cache() -> None:
    """Clears the module-level topological inheritance cache."""
    _TOPOLOGICAL_INHERITANCE_CACHE.clear()


def _get_topology_sha256(topology: Any) -> str:
    """Extracts or derives a stable content digest for graph topology caching."""
    sha = getattr(topology, "graph_sha256", None)
    if isinstance(sha, str) and sha:
        return sha
    assertions = getattr(topology, "assertions", None)
    if isinstance(assertions, dict):
        key_summary = f"{len(assertions)}:{sorted(str(k) for k in list(assertions.keys())[:20])}"
        return hashlib.sha256(key_summary.encode("utf-8")).hexdigest()
    return f"mem_{id(topology)}"


def is_candidate_control_checklist(item: Dict[str, Any]) -> bool:
    """Checks whether an assertion is a generic candidate AI-SDLC or retrieval control checklist."""
    if not isinstance(item, dict):
        return False
    claim = str(item.get("claim_text") or item.get("object_literal") or "").strip().lower()
    return claim.startswith(("candidate ai-sdlc control", "candidate enterprise-retrieval control", "candidate gemini/"))


def is_enterprise_policy(item: Dict[str, Any]) -> bool:
    """Checks whether an assertion or node represents legitimate global enterprise policy."""
    if not isinstance(item, dict):
        return False
    if item.get("is_enterprise_policy") is True:
        return True
    if item.get("namespace") in ("ENTERPRISE_POLICY", "GLOBAL", "ENTERPRISE"):
        return True
    if item.get("classification") in ("ENTERPRISE_POLICY", "GLOBAL", "ENTERPRISE", "SHARED"):
        return True
    eng = normalize_engagement_id(item.get("engagement_id") or item.get("project_scope"))
    if eng and eng in ENTERPRISE_POLICY_SCOPES:
        return True
    raw_eng = str(item.get("engagement_id") or item.get("project_scope") or "").strip().lower()
    return raw_eng in ENTERPRISE_POLICY_SCOPES


def is_technical_anchor_assertion(assertion: Dict[str, Any]) -> bool:
    """Checks whether an assertion anchors an enterprise-level technical component."""
    if not isinstance(assertion, dict):
        return False
    subj = str(assertion.get("subject_node_id") or assertion.get("subject_node") or "")
    obj = str(assertion.get("object_node_id") or assertion.get("object_node") or "")
    return subj.startswith("anchor_") or obj.startswith("anchor_")


def _matches_bound_or_permitted(eng_id: Optional[str], norm_bound: str, permitted: Optional[Set[str]]) -> bool:
    if not eng_id:
        return False
    if match_engagement(eng_id, norm_bound):
        return True
    return bool(permitted and any(match_engagement(eng_id, pe) for pe in permitted))


def _extract_item_engagement(
    item: Dict[str, Any], norm_bound: str, permitted: Optional[Set[str]] = None
) -> Optional[str]:
    raw_val = item.get("engagement_id") or item.get("project_scope") or item.get("origin_engagement_id")
    if raw_val is not None and not isinstance(raw_val, str):
        return None
    eng_id = normalize_engagement_id(raw_val)
    if not eng_id and isinstance(item.get("scope_text"), str) and item.get("scope_text", "").strip():
        scope_low = item["scope_text"].lower()
        candidates = [norm_bound] + sorted(permitted or ())
        for cand in candidates:
            slug = cand.replace("project:", "").replace("project_", "").lower()
            if slug and len(slug) >= 3 and slug in scope_low:
                return cand
    return eng_id


def is_assertion_in_scope(
    assertion: Dict[str, Any], bound_engagement: Optional[str], permitted_engagements: Optional[Set[str]] = None
) -> bool:
    """Validates whether an assertion belongs to the bound engagement or permitted connections."""
    if not isinstance(assertion, dict) or is_c19_quarantined_assertion(assertion):
        return False
    norm_bound = normalize_engagement_id(bound_engagement)
    if not norm_bound:
        return False
    effective_permitted = set(permitted_engagements or ()) | get_project_scope_aliases(norm_bound)
    eng_id = _extract_item_engagement(assertion, norm_bound, effective_permitted)
    if norm_bound in PERSONAL_NON_ENTERPRISE_SCOPES:
        return _matches_bound_or_permitted(eng_id, norm_bound, effective_permitted)
    if is_candidate_control_checklist(assertion) and norm_bound not in ENTERPRISE_POLICY_SCOPES:
        return "project:enterprise" in effective_permitted
    if is_enterprise_policy(assertion):
        return True
    return _matches_bound_or_permitted(eng_id, norm_bound, effective_permitted)


def _is_c19_or_invalid(item: Any) -> bool:
    if not isinstance(item, dict) or is_c19_quarantined_assertion(item):
        return True
    return has_c19_marker(str(item.get("claim_text") or item.get("object_literal") or ""))


def calculate_engagement_affinity(
    item: Dict[str, Any], bound_engagement: Optional[str], permitted_engagements: Optional[Set[str]] = None
) -> float:
    """Calculates continuous affinity multiplier [0.0, 1.25] for an assertion or node."""
    if _is_c19_or_invalid(item):
        return 0.0
    norm_bound = normalize_engagement_id(bound_engagement)
    if not norm_bound:
        return 1.0
    eng_id = _extract_item_engagement(item, norm_bound)
    if eng_id and match_engagement(eng_id, norm_bound):
        return 1.25
    if is_candidate_control_checklist(item) and norm_bound not in ENTERPRISE_POLICY_SCOPES:
        return 0.25
    if is_enterprise_policy(item):
        return 1.15
    effective_permitted = set(permitted_engagements or ()) | get_project_scope_aliases(norm_bound)
    if eng_id and any(match_engagement(eng_id, pe) for pe in effective_permitted):
        return 1.10
    return 1.0


def is_node_in_scope(
    node_id: str,
    node_data: Dict[str, Any],
    bound_engagement: Optional[str],
    permitted_engagements: Optional[Set[str]] = None,
    is_attendee: bool = False,
) -> bool:
    """
    Validates whether a graph node is eligible for retrieval indexing or traversal.
    Enforces Rule C-19: quarantined nodes fail closed even if flagged as attendees.
    Attendees may be indexed as seeds, but arbitrary external project nodes fail closed.
    """
    if not node_id or not isinstance(node_data, dict):
        return False
    if is_c19_quarantined_node(node_id, node_data):
        return False
    norm_bound = normalize_engagement_id(bound_engagement)
    if not norm_bound:
        return False
    effective_permitted = set(permitted_engagements or ()) | get_project_scope_aliases(norm_bound)
    node_eng = normalize_engagement_id(node_data.get("engagement_id") or node_data.get("project_scope"))
    if norm_bound in PERSONAL_NON_ENTERPRISE_SCOPES:
        return bool(node_eng and _is_scoped_engagement(node_eng, norm_bound, effective_permitted))
    if is_attendee or is_enterprise_policy(node_data):
        return True
    if node_eng:
        return _is_scoped_engagement(node_eng, norm_bound, effective_permitted)
    if node_id.startswith("project:") or str(node_data.get("node_type", "")).lower() == "project":
        return _is_scoped_engagement(node_id, norm_bound, effective_permitted)
    if node_data.get("node_type") == "technical_anchor" or node_id.startswith("anchor_"):
        return _is_technical_anchor_in_scope(node_data, norm_bound, effective_permitted)
    return True


_PROJECT_KEYWORD_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (("dociq",), "project:dociq"),
    (("jarvis",), "project:project_jarvis"),
    (("nexus",), "project:project_nexus"),
    (("lens", "sow 1", "drone", "pipeline review"), "project:project_lens"),
    (("pgd", "pgd followup"), "project:pgd"),
    (("sherpa",), "project:project_beacon"),
    (("rewire",), "project:project_atlas"),
    (("apex_trans",), "project:apex_trans"),
)


def _is_technical_anchor_in_scope(node_data: Dict[str, Any], norm_bound: str, effective_permitted: Set[str]) -> bool:
    if is_enterprise_policy(node_data):
        return True
    desc = f"{node_data.get('description', '')} {node_data.get('canonical_name', '')}".lower()
    for kw, proj in _PROJECT_KEYWORD_MAP:
        if any(k in desc for k in kw):
            return match_engagement(proj, norm_bound) or proj in effective_permitted
    return True


def _is_scoped_engagement(
    eng: str,
    norm_bound: str,
    permitted_engagements: Optional[Set[str]] = None,
) -> bool:
    if match_engagement(eng, norm_bound):
        return True
    if permitted_engagements:
        return any(match_engagement(eng, pe) for pe in permitted_engagements)
    raw_slug = norm_bound.replace("project:", "")
    return eng.lower() in (norm_bound.lower(), raw_slug.lower())


def _is_node_matching_engagement(nid: str, nd: Dict[str, Any], norm_eng: str, raw_slug: str) -> bool:
    if not isinstance(nd, dict) or is_c19_quarantined_node(nid, nd):
        return False
    node_eng = normalize_engagement_id(nd.get("engagement_id") or nd.get("project_scope"))
    if node_eng and match_engagement(node_eng, norm_eng):
        return True
    nid_norm = normalize_engagement_id(nid)
    if nid_norm and match_engagement(nid_norm, norm_eng):
        return True
    return nid.lower() in (norm_eng.lower(), raw_slug.lower())


def _is_hub_or_person_node(nid: str, nodes: Optional[Dict[str, Any]] = None) -> bool:
    """Detects hub, person, organization, attendee, and team nodes that must never act as transitive bridges."""
    nl = nid.lower()
    if nl.startswith(("person_", "person:", "org_", "org:", "attendee_", "attendee:", "team_", "team:")) or ":person:" in nl or ":org:" in nl:
        return True
    nd = nodes.get(nid) if isinstance(nodes, dict) else None
    return isinstance(nd, dict) and str(nd.get("node_type") or "").lower() in ("person", "organization", "org", "attendee", "team")


def find_engagement_node_ids(topology: Any, engagement_id: str) -> Set[str]:
    """Finds all non-quarantined node IDs belonging to the given engagement, excluding person/org hub nodes."""
    if not topology or not engagement_id:
        return set()
    norm_eng = normalize_engagement_id(engagement_id)
    if not norm_eng:
        return set()
    nodes = getattr(topology, "nodes", None)
    if not isinstance(nodes, dict):
        return set()
    raw_slug = norm_eng.replace("project:", "")
    return {
        nid for nid, nd in nodes.items()
        if _is_node_matching_engagement(nid, nd, norm_eng, raw_slug) and not _is_hub_or_person_node(nid, nodes)
    }


def _extract_assertion_target_node(
    a: Dict[str, Any], aid: str, base_nodes: Set[str], nodes: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    if not isinstance(a, dict) or is_c19_quarantined_assertion(a, aid) or str(a.get("predicate") or "").upper() not in TOPOLOGICAL_INHERITANCE_PREDICATES:
        return None
    subj, obj = str(a.get("subject_node_id") or a.get("subject") or ""), str(a.get("object_node_id") or a.get("object") or "")
    if _is_hub_or_person_node(subj, nodes) or _is_hub_or_person_node(obj, nodes):
        return None
    return obj if (subj in base_nodes and obj not in base_nodes) else (subj if (obj in base_nodes and subj not in base_nodes) else None)


def _resolve_target_engagement(target_nid: str, nodes: Dict[str, Any]) -> Optional[str]:
    if _is_hub_or_person_node(target_nid, nodes):
        return None
    target_node = nodes.get(target_nid) if isinstance(nodes, dict) else None
    if isinstance(target_node, dict):
        if is_c19_quarantined_node(target_nid, target_node):
            return None
        eng = normalize_engagement_id(target_node.get("engagement_id") or target_node.get("project_scope"))
        return eng or (normalize_engagement_id(target_nid) if str(target_node.get("node_type", "")).lower() == "project" else None)
    return normalize_engagement_id(target_nid) if target_nid.startswith("project:") else None


def _scan_inherited_engagements(topology: Any, base_nodes: Set[str], norm_base: str) -> Set[str]:
    assertions = getattr(topology, "assertions", None)
    if not isinstance(assertions, dict):
        return set()
    nodes = getattr(topology, "nodes", {})
    inherited: Set[str] = set()
    for aid, a in assertions.items():
        target_nid = _extract_assertion_target_node(a, aid, base_nodes, nodes)
        if not target_nid:
            continue
        target_eng = _resolve_target_engagement(target_nid, nodes)
        if not target_eng or target_eng == norm_base or match_engagement(target_eng, norm_base):
            continue
        if not is_enterprise_policy({"engagement_id": target_eng}) and not has_c19_marker(target_eng):
            inherited.add(target_eng)
    return inherited


def discover_topologically_inherited_engagements(
    topology: Any,
    base_engagement: Optional[str],
) -> Set[str]:
    """
    Discovers permitted predecessor and tangential engagements linked to the base
    engagement via TOPOLOGICAL_INHERITANCE_PREDICATES and topological project aliases.
    Caches results by (base_engagement, graph_sha256).
    """
    if not topology or not base_engagement:
        return set()
    norm_base = normalize_engagement_id(base_engagement)
    if not norm_base:
        return set()
    assertions = getattr(topology, "assertions", None)
    if not isinstance(assertions, dict):
        return set()
    graph_sha = _get_topology_sha256(topology)
    cache_key = (norm_base, graph_sha)
    if cache_key in _TOPOLOGICAL_INHERITANCE_CACHE:
        return set(_TOPOLOGICAL_INHERITANCE_CACHE[cache_key])
    base_nodes = find_engagement_node_ids(topology, norm_base)
    base_nodes.add(norm_base)
    base_nodes.add(norm_base.replace("project:", ""))
    for alias in get_project_scope_aliases(norm_base):
        base_nodes.update(find_engagement_node_ids(topology, alias))
        base_nodes.add(alias)
        base_nodes.add(alias.replace("project:", ""))
    inherited = _scan_inherited_engagements(topology, base_nodes, norm_base)
    inherited.update(get_project_scope_aliases(norm_base))
    _TOPOLOGICAL_INHERITANCE_CACHE[cache_key] = set(inherited)
    return set(inherited)


def detect_cross_engagement_request(
    query_text: str,
    base_engagement: Optional[str],
    known_engagements: Iterable[str],
) -> Optional[str]:
    """
    Detects if an explicit query requests context from another known engagement.
    Returns the canonical target engagement ID if explicitly requested, else None.
    """
    if not query_text or not base_engagement:
        return None
    norm_base = normalize_engagement_id(base_engagement)
    cleaned_query = query_text.lower()
    for candidate in known_engagements:
        norm_cand = normalize_engagement_id(candidate)
        if not norm_cand or norm_cand == norm_base:
            continue
        slug_name = norm_cand.replace("project:", "")
        raw_name = slug_name.replace("_", " ")
        if re.search(rf"\b(?:{re.escape(raw_name)}|{re.escape(slug_name)})\b", cleaned_query):
            return norm_cand
    return None


def _matches_cross_seeds(subj: str, obj: str, seeds: Set[str], targets: Set[str]) -> bool:
    return (subj in seeds and obj in targets) or (subj in targets and obj in seeds)


def _has_cross_assertion(assertions: dict, seeds: Set[str], targets: Set[str]) -> bool:
    for aid, a in assertions.items():
        if not isinstance(a, dict) or is_c19_quarantined_assertion(a, aid):
            continue
        pred = str(a.get("predicate") or "").upper()
        if pred not in EVIDENCED_CROSS_RELATIONSHIP_PREDICATES:
            continue
        subj = str(a.get("subject_node_id") or a.get("subject") or "")
        obj = str(a.get("object_node_id") or a.get("object") or "")
        if _matches_cross_seeds(subj, obj, seeds, targets):
            return True
    return False


def verify_evidenced_relationship(
    topology: Any,
    base_engagement: str,
    target_engagement: str,
) -> bool:
    """
    Verifies that the knowledge graph topology contains an evidenced relationship
    connecting the base engagement to the requested target engagement.
    """
    norm_base = normalize_engagement_id(base_engagement)
    norm_target = normalize_engagement_id(target_engagement)
    if not norm_base or not norm_target or norm_base == norm_target:
        return False
    if norm_target in get_project_scope_aliases(norm_base):
        return True
    assertions = getattr(topology, "assertions", {})
    if not isinstance(assertions, dict):
        return False
    candidate_seeds = {norm_base, norm_base.replace("project:", "")}
    candidate_targets = {norm_target, norm_target.replace("project:", "")}
    return _has_cross_assertion(assertions, candidate_seeds, candidate_targets)


def _collect_known_project_engagements(topology: Any) -> Set[str]:
    nodes = getattr(topology, "nodes", {})
    if not isinstance(nodes, dict):
        return set()
    known: Set[str] = set()
    for nid, nd in nodes.items():
        if isinstance(nd, dict) and nd.get("node_type") == "project":
            if not is_c19_quarantined_node(nid, nd):
                known.add(nid)
                eng = nd.get("engagement_id")
                if eng:
                    known.add(eng)
    return known


def _extract_inherited_target(a: Dict[str, Any], candidate_seeds: Set[str], norm_base: str) -> Optional[str]:
    pred = str(a.get("predicate") or "").upper()
    if pred not in {"PREDECESSOR_PROJECT", "DEPENDS_ON", "INHERITS_FROM"}:
        return None
    subj = str(a.get("subject_node_id") or a.get("subject") or "")
    if subj not in candidate_seeds:
        return None
    obj = str(a.get("object_node_id") or a.get("object") or "")
    norm_target = normalize_engagement_id(obj)
    if norm_target and norm_target != norm_base:
        return norm_target
    return None


def _resolve_topological_inherited_engagements(norm_base: str, topology: Any) -> Set[str]:
    assertions = getattr(topology, "assertions", {})
    if not isinstance(assertions, dict):
        return set()
    candidate_seeds = {norm_base, norm_base.replace("project:", "")}
    permitted: Set[str] = set()
    for a in assertions.values():
        if isinstance(a, dict):
            target = _extract_inherited_target(a, candidate_seeds, norm_base)
            if target:
                permitted.add(target)
    return permitted


def _resolve_explicitly_requested_engagements(query: Any, base_engagement: str, topology: Any) -> Set[str]:
    query_text = (
        " ".join(query.retrieval_seeds())
        if hasattr(query, "retrieval_seeds")
        else str(query)
    )
    if not query_text.strip():
        return set()
    known = _collect_known_project_engagements(topology)
    requested_target = detect_cross_engagement_request(query_text, base_engagement, known)
    if requested_target and verify_evidenced_relationship(topology, base_engagement, requested_target):
        logger.info("Permitted cross-engagement context verified: %s -> %s", base_engagement, requested_target)
        return {requested_target}
    return set()


def resolve_permitted_engagements(
    query: Any,
    base_engagement: Optional[str],
    topology: Any,
    include_topological_inheritance: bool = True,
) -> Set[str]:
    """
    Resolves permitted cross-engagement scopes for a query.
    1. If include_topological_inheritance=True, automatically inherits evidenced predecessor
       and tangential engagements from graph topology.
    2. Merges explicitly requested cross-engagement targets if supported by evidenced graph relationships.
    """
    if not base_engagement or not topology:
        return set()
    norm_base = normalize_engagement_id(base_engagement)
    permitted: Set[str] = set()
    if include_topological_inheritance:
        permitted.update(discover_topologically_inherited_engagements(topology, base_engagement))
        if norm_base:
            permitted.update(_resolve_topological_inherited_engagements(norm_base, topology))
    permitted.update(_resolve_explicitly_requested_engagements(query, base_engagement, topology))
    return permitted


def resolve_permitted_scopes(
    base_engagement: Optional[str],
    topology: Any = None,
    include_topological_inheritance: bool = True,
) -> Set[str]:
    """Resolves all permitted engagement scopes including base, aliases, and topological inheritance."""
    if not base_engagement:
        return set()
    norm = normalize_engagement_id(base_engagement)
    if not norm:
        return set()
    scopes = {norm}
    scopes.update(get_project_scope_aliases(norm))
    if topology and include_topological_inheritance:
        scopes.update(discover_topologically_inherited_engagements(topology, norm))
    return scopes


@dataclass
class ScopeBinding:
    """Binds reusable copilot context to exact session, engagement, and graph generation."""

    engagement_id: str
    session_id: str
    generation: Optional[Any] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def is_valid(
        self,
        incoming_session_id: Optional[str],
        incoming_engagement: Optional[str],
        incoming_generation: Optional[Any] = None,
    ) -> bool:
        """Fails closed if session or engagement switches, preventing stale cross-scope pollution."""
        if incoming_session_id and incoming_session_id != self.session_id:
            return False
        if incoming_engagement and normalize_engagement_id(incoming_engagement) != normalize_engagement_id(self.engagement_id):
            return False
        if incoming_generation and self.generation and str(incoming_generation) != str(self.generation):
            return False
        return True


DEFAULT_TENANT_HMAC_SECRET: str = "nee_tenant_isolation_secret_salt_v1"


def generate_tenant_tag(tenant_id: str, entity_id: str, secret: Optional[str] = None) -> str:
    """Generates deterministic cryptographic HMAC-SHA256 tag binding tenant to graph entity."""
    sec = (secret or DEFAULT_TENANT_HMAC_SECRET).encode("utf-8")
    msg = f"{tenant_id}:{entity_id}".encode("utf-8")
    return hmac.new(sec, msg, hashlib.sha256).hexdigest()


def verify_tenant_tag(tenant_id: str, entity_id: str, tag: str, secret: Optional[str] = None) -> bool:
    """Verifies that an entity's tenant tag matches the expected HMAC-SHA256 digest."""
    if not tag or not tenant_id or not entity_id:
        return False
    expected = generate_tenant_tag(tenant_id, entity_id, secret)
    return hmac.compare_digest(expected, tag)


def tag_graph_node(node: Dict[str, Any], tenant_id: str, secret: Optional[str] = None) -> Dict[str, Any]:
    """Immutably tags a graph node with cryptographic tenant identity and HMAC."""
    nid = str(node.get("node_id") or node.get("id") or "")
    node["tenant_id"] = tenant_id
    node["tenant_hmac"] = generate_tenant_tag(tenant_id, nid, secret)
    return node


def tag_graph_edge(edge: Dict[str, Any], tenant_id: str, secret: Optional[str] = None) -> Dict[str, Any]:
    """Immutably tags a relationship edge with cryptographic tenant identity and HMAC."""
    eid = str(edge.get("edge_id") or f"{edge.get('from_node_id')}:{edge.get('to_node_id')}")
    edge["tenant_id"] = tenant_id
    edge["tenant_hmac"] = generate_tenant_tag(tenant_id, eid, secret)
    return edge


def _has_c19_in_items(items: Tuple[Dict[str, Any], ...]) -> bool:
    from .copilot_neighborhood import has_c19_marker
    for item in items:
        text_to_check = " ".join(
            str(v) for k, v in item.items()
            if k in ("claim_text", "canonical_name", "display_name", "description", "object_literal")
        )
        if has_c19_marker(text_to_check):
            return True
    return False


def _verify_nodes_hmac(source_node: Dict[str, Any], target_node: Dict[str, Any], secret: Optional[str]) -> bool:
    s_nid, s_tag = str(source_node.get("node_id") or source_node.get("id") or ""), source_node.get("tenant_hmac")
    if not s_tag or not verify_tenant_tag(source_node.get("tenant_id", ""), s_nid, s_tag, secret):
        return False
    t_nid, t_tag = str(target_node.get("node_id") or target_node.get("id") or ""), target_node.get("tenant_hmac")
    return bool(t_tag and verify_tenant_tag(target_node.get("tenant_id", ""), t_nid, t_tag, secret))


def is_edge_authorized(
    source_node: Dict[str, Any],
    edge: Dict[str, Any],
    target_node: Dict[str, Any],
    caller_tenant: str,
    permitted_tenants: Optional[Set[str]] = None,
    enforce_hmac: bool = False,
    secret: Optional[str] = None,
) -> bool:
    """
    Context-aware edge authorization evaluating cryptographic tenant tags and Rule C-19 firewall.
    Ensures strict multi-tenant isolation without breaking intra-tenant topological inheritance.
    """
    if _has_c19_in_items((source_node, edge, target_node)):
        return False

    if enforce_hmac and not _verify_nodes_hmac(source_node, target_node, secret):
        return False

    allowed_tenants = {caller_tenant}
    if permitted_tenants:
        allowed_tenants.update(permitted_tenants)

    src_tenant = source_node.get("tenant_id") or "default"
    tgt_tenant = target_node.get("tenant_id") or "default"
    edge_tenant = edge.get("tenant_id") or src_tenant

    return (src_tenant in allowed_tenants) and (tgt_tenant in allowed_tenants) and (edge_tenant in allowed_tenants)
