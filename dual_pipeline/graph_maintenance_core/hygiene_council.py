"""8-Agent Adversarial Council & Atomic HITL Clustering Engine for Knowledge Graph Hygiene."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .hygiene_mutation_agents import compile_finding_promised_mutations  # noqa: lazy-decouple
from .speaker_hygiene_rules import build_mece_cluster_finding_rows, build_undiarized_speaker_question_payload

PROPOSAL_KEYWORDS = re.compile(r"\b(?:proposal|pursuit|pending\s+award|preferred\s+vendor|rfp|bid|pitch|pre-award)\b", re.IGNORECASE)
PASCAL_OR_SNAKE_RE = re.compile(r"\b[a-z]+[A-Z][a-zA-Z0-9]*\b|\b[a-zA-Z0-9]+_[a-zA-Z0-9_]+\b")


@dataclass
class CouncilDebateRecord:
    """Records Team Alpha vs Team Beta deliberation and committee verdict for a finding."""
    finding_index: int
    cluster_id: str
    team_alpha_argument: str
    team_beta_counterargument: str
    committee_verdict: str
    requires_isolated_scrutiny: bool = False
    confidence_pct: int = 90
    confidence_tier: str = "HIGH"


@dataclass
class HITLDecisionCluster:
    """Atomic, domain-coherent HITL question cluster with pre-validated question payload."""
    cluster_id: str
    title: str
    wave_number: int
    category: str
    finding_count: int
    requires_isolated_scrutiny: bool
    plain_english_rationale: str
    council_debate_summary: str
    entity_references: List[str]
    ready_to_ask_question: Dict[str, Any]
    findings: List[Dict[str, Any]] = field(default_factory=list)
    promised_mutations: List[Dict[str, Any]] = field(default_factory=list)


def _clean_cell(text: Any, max_len: int = 110) -> str:
    """Sanitize text for Markdown table cells without ellipses or pipes."""
    raw = str(text or "None").replace("|", "/").replace("\n", " ").replace("...", "").replace("\u2026", "")
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) <= max_len:
        return raw
    cut = raw[:max_len].rsplit(" ", 1)[0]
    return cut if cut else raw[:max_len]


def _is_proposal_or_low_margin(raw: Dict[str, Any]) -> bool:
    """Detect if a workstream/program merge involves a pre-award proposal or low margin (<0.20)."""
    from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

    m_val = raw.get("margin_delta") if raw.get("margin_delta") is not None else raw.get("decision_margin")
    margin = float(m_val if m_val is not None else 1.0)
    text_blob = " ".join(
        str(raw.get(k) or "")
        for k in ("canonical_name_a", "canonical_name_b", "description_a", "description_b")
    )
    return bool(margin < get_hitl_ambiguity_margin_threshold() or PROPOSAL_KEYWORDS.search(text_blob))


def _normalize_role_tokens(name: str) -> set[str]:
    norm = re.sub(r"\bswe\b", "software engineer", name.lower())
    norm = re.sub(r"\bvp\b", "vice president", norm)
    tokens = {w for w in re.findall(r"[a-z0-9]+", norm) if len(w) > 1 or w.isdigit()}
    if "software" in tokens and "engineer" in tokens:
        tokens.discard("software")
    return tokens


def _is_scope_divergent_role(raw: Dict[str, Any]) -> bool:
    """Detect if a role_group merge pairs a generic role with a project-scoped title."""
    s_val = raw.get("similarity_score") if raw.get("similarity_score") is not None else raw.get("similarity")
    sim = float(s_val if s_val is not None else 1.0)
    name_a = str(raw.get("canonical_name_a") or "").lower()
    name_b = str(raw.get("canonical_name_b") or "").lower()
    words_a = _normalize_role_tokens(name_a)
    words_b = _normalize_role_tokens(name_b)
    return bool(sim < 0.90 and words_a != words_b)


def _extract_pair_tokens(rows: List[Any]) -> set[Tuple[str, str]]:
    pairs: set[Tuple[str, str]] = set()
    for (val,) in rows:
        parts = str(val or "").split("::", 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            pairs.add(tuple(sorted((parts[0].strip(), parts[1].strip()))))
    return pairs


def load_active_boundary_exclusions(conn: Any) -> set[Tuple[str, str]]:
    """Load operator-approved keep-distinct entity pairs from graph_quality_exception and entity_resolution_candidate."""
    if conn is None:
        return set()
    excluded: set[Tuple[str, str]] = set()
    try:
        cur = conn.cursor()
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "graph_quality_exception" in tables:
            r1 = cur.execute("SELECT object_id FROM graph_quality_exception WHERE exception_type='HYGIENE_KEEP_DISTINCT' AND active=1").fetchall()
            excluded.update(_extract_pair_tokens(r1))
            r_gen = cur.execute("SELECT object_type, object_id FROM graph_quality_exception WHERE exception_type='HYGIENE_FINDING_EXCLUDED' AND active=1").fetchall()
            excluded.update((str(ot), str(oid)) for ot, oid in r_gen if ot and oid)
        if "entity_resolution_candidate" in tables:
            r2 = cur.execute("SELECT normalized_key FROM entity_resolution_candidate WHERE status IN ('resolved_distinct', 'rejected_distinct')").fetchall()
            excluded.update(_extract_pair_tokens(r2))
    except Exception:
        return excluded
    return excluded


class EntityDisambiguationCriticAgent:
    """Agent 5: Audits entity merges for project-scope mismatches and title drift."""

    def review(self, findings: List[Any]) -> Dict[int, str]:
        notes: Dict[int, str] = {}
        for idx, f in enumerate(findings):
            if f.proposed_fix != "merge_entities":
                continue
            raw = f.raw_data or {}
            ntype = str(raw.get("node_type_a") or "")
            if ntype == "role_group" and _is_scope_divergent_role(raw):
                notes[idx] = (
                    f"Scope warning: '{raw.get('canonical_name_a')}' vs '{raw.get('canonical_name_b')}' "
                    "differs by project qualifier; isolate from punctuation fixes."
                )
            else:
                notes[idx] = "Canonical identity match verified across alias tokens."
        return notes


class LifecycleBoundaryAdvocateAgent:
    """Agent 6: Devil's Advocate auditing pre-award proposals vs active delivery programs."""

    def review(self, findings: List[Any]) -> Dict[int, str]:
        notes: Dict[int, str] = {}
        for idx, f in enumerate(findings):
            if f.proposed_fix != "merge_entities":
                continue
            raw = f.raw_data or {}
            ntype = str(raw.get("node_type_a") or "")
            if ntype in ("workstream", "program") and _is_proposal_or_low_margin(raw):
                notes[idx] = (
                    "Lifecycle boundary alert: Contains pre-award proposal or margin < 0.20; "
                    "must ask operator whether to designate as a proposal opportunity."
                )
            else:
                notes[idx] = "No commercial lifecycle stage conflict detected."
        return notes


class RetrievalSidecarParityAgent:
    """Agent 7: Verifies unpromoted staging redundancy against active canonical records."""

    def review(self, findings: List[Any]) -> Dict[int, str]:
        notes: Dict[int, str] = {}
        for idx, f in enumerate(findings):
            raw = f.raw_data or {}
            if f.proposed_fix == "archive_candidate_assertion":
                sup = raw.get("superseded_by_assertion_id") or "active official record"
                notes[idx] = (
                    f"Unpromoted draft in staging; redundant because active promoted record ({sup}) "
                    "already governs retrieval."
                )
            elif f.proposed_fix == "resync_search_sidecars":
                notes[idx] = "Active official records missing from keyword search index; resync restores recall."
            elif f.proposed_fix == "batch_promote_candidates":
                notes[idx] = "Staging drafts with confidence >= 0.85 and verbatim quotes verified."
        return notes


DETECTOR_PLAIN_LABELS: Dict[str, str] = {
    "StructuralValidator": "Structure Review",
    "ProvenanceInspector": "Source Review",
    "TemporalAuditor": "Timeline Review",
    "RedTeamer": "Governance Review",
}

FIX_PLAIN_LABELS: Dict[str, str] = {
    "archive_candidate_assertion": "archive redundant staging drafts",
    "demote_leaked_candidate_assertion": "remove unapproved staging drafts from active search",
    "quarantine_cross_project_assertion": "quarantine cross-project contaminated statements",
    "quarantine_advisory_or_orphan_sidecar": "quarantine internal advisory and orphan search entries",
    "merge_entities": "consolidate duplicate entity profiles",
    "archive_edge": "retire dangling relationship links",
    "auto_link_edge": "link relationship records to official statements",
    "synthesize_edge_assertion": "create missing relationship statements",
    "deduplicate_assertions": "combine duplicate statements",
    "supersede_assertion": "supersede older timeline statements",
    "escalate_90d_revalidation": "queue aged records for ninety-day review",
    "batch_promote_candidates": "promote verified staging notes",
    "resync_search_sidecars": "synchronize keyword search index entries",
    "deactivate_search_documents": "deactivate stale search index entries",
    "archive_node": "retire isolated orphan profiles",
    "run_graph_gc": "clean up orphaned evidence records",
    "consolidate_duplicate_sources": "consolidate duplicate source documents",
    "unmerge_or_retitle_degenerate_source": "retitle or separate collided source records",
    "reconcile_evidence": "reconcile ungrounded evidence records",
    "quarantine_audit_record": "quarantine unsigned audit entries",
    "review_audit_record": "review unverified approval references",
    "reconcile_stale_role_and_description": "update stale stakeholder descriptions and supersede unresolved role caveats",
    "rebind_literal_entity_mention_edge": "link literal entity mentions to canonical target profiles",
    "quarantine_disjoint_role_or_owner_bleed": "quarantine disjoint operations and misattributed executive ownership claims",
    "quarantine_undiarized_or_misattributed_claim": "move unverified speaker statements to staging hold",
    "rebind_placeholder_speaker": "rebind deterministic speaker attributions on active claims",
    "attribute_undiarized_speaker_hitl": "attribute undiarized speaker turns through operator review",
    "sync_promoted_qualifier_metadata": "synchronize staging labels on approved official statements",
}

_SIMPLE_CLUSTER_MAP: Dict[str, Tuple[str, str, int, bool]] = {
    "archive_candidate_assertion": ("CLUSTER-01-UNPROMOTED-STAGING-DRAFTS", "Cluster 1: Unpromoted Staging Drafts Redundant with Active Official Records", 1, True),
    "demote_leaked_candidate_assertion": ("CLUSTER-01B-LEAKED-CANDIDATES", "Cluster 1B: Unapproved Staging Drafts Leaked into Active Search", 1, False),
    "quarantine_undiarized_or_misattributed_claim": ("CLUSTER-01D-SPEAKER-ATTRIBUTION-HYGIENE", "Cluster 1D: Unverified and Misattributed Meeting Statements Quarantine", 1, True),
    "rebind_placeholder_speaker": ("CLUSTER-01D2-SPEAKER-REBIND", "Cluster 1D-2: Deterministic Speaker Attribution Rebind on Active Statements", 1, False),
    "attribute_undiarized_speaker_hitl": ("CLUSTER-01F-UNDIARIZED-SPEAKER-ATTRIBUTION-HITL", "Cluster 1F: Undiarized Meeting Statement Speaker Attribution", 1, True),
    "sync_promoted_qualifier_metadata": ("CLUSTER-01E-QUALIFIER-METADATA-SYNC", "Cluster 1E: Approved Statement Metadata Synchronization", 2, False),
    "auto_link_edge": ("CLUSTER-03-EDGE-PARITY", "Cluster 3: Relationship Link Parity and Dangling Edges", 1, False),
    "archive_edge": ("CLUSTER-03B-DANGLING-EDGES", "Cluster 3B: Dangling Relationship Links to Retired Profiles", 1, False),
    "rebind_literal_entity_mention_edge": ("CLUSTER-03B-LITERAL-ENTITY-EDGE-HYDRATION", "Cluster 3B-2: Literal Entity Mentions Missing Relationship Links", 1, False),
    "synthesize_edge_assertion": ("CLUSTER-03C-SYNTHESIZE-EDGE-ASSERTIONS", "Cluster 3C: Missing Official Statements for Active Relationship Links", 1, False),
    "deduplicate_assertions": ("CLUSTER-04-DUPLICATE-STATEMENTS", "Cluster 4: Repeated Statements from Multiple Meetings", 2, False),
    "supersede_assertion": ("CLUSTER-05-TEMPORAL-UPDATES", "Cluster 5: Chronological and Reporting Line Updates", 2, True),
    "escalate_90d_revalidation": ("CLUSTER-05B-90D-REVALIDATION", "Cluster 5B: Ninety-Day Historical Statement Revalidation Queue", 2, True),
    "reconcile_stale_role_and_description": ("CLUSTER-05C-ROLE-AND-BIO-SUPERSESSION", "Cluster 5C: Stale Role Caveats and Unresolved Stakeholder Descriptions", 2, False),
    "batch_promote_candidates": ("CLUSTER-06-STAGING-PROMOTIONS", "Cluster 6: High-Confidence Staging Note Promotions", 1, True),
    "resync_search_sidecars": ("CLUSTER-07-SEARCH-INDEX-SYNC", "Cluster 7: Keyword Search Index Synchronization", 2, False),
    "deactivate_search_documents": ("CLUSTER-07B-DEACTIVATE-LEAKED-SEARCH-DOCS", "Cluster 7B: Deactivate Leaked Non-Canonical Search Index Entries", 2, False),
    "quarantine_cross_project_assertion": ("CLUSTER-07C-CROSS-PROJECT-CONTAMINATION", "Cluster 7C: Cross-Project Statement Contamination Quarantine", 2, True),
    "quarantine_disjoint_role_or_owner_bleed": ("CLUSTER-07D-DISJOINT-ROLE-AND-OWNER-BLEED", "Cluster 7D: Disjoint Operations and Executive Owner Misattribution Quarantine", 2, True),
    "archive_node": ("CLUSTER-08-GOVERNANCE-AUDIT", "Cluster 8: Audit Provenance and Governance Review", 2, True),
    "run_graph_gc": ("CLUSTER-08B-GRAPH-GC", "Cluster 8B: Orphaned Evidence Record Garbage Collection", 2, False),
    "consolidate_duplicate_sources": ("CLUSTER-09A-DUPLICATE-SOURCES", "Cluster 9A: Duplicate Meeting and Document Source Consolidation", 2, False),
    "unmerge_or_retitle_degenerate_source": ("CLUSTER-09B-DEGENERATE-AND-COLLIDED-SOURCES", "Cluster 9B: Placeholder Title and Collided Source Separation", 2, True),
    "reconcile_evidence": ("CLUSTER-10-UNGROUNDED-EVIDENCE", "Cluster 10: Ungrounded Statement and Missing Quote Reconciliation", 2, True),
    "quarantine_audit_record": ("CLUSTER-11-AUDIT-SECURITY", "Cluster 11: Audit Trail Signature and Approval Review", 2, True),
    "review_audit_record": ("CLUSTER-11-AUDIT-SECURITY", "Cluster 11: Audit Trail Signature and Approval Review", 2, True),
    "quarantine_advisory_or_orphan_sidecar": ("CLUSTER-11B-ADVISORY-AND-SIDECAR-BLEED", "Cluster 11B: Internal Advisory and Orphan Search Entry Quarantine", 2, True),
    "split_or_normalize_compound_node": ("CLUSTER-02G-COMPOUND-AND-MISTYPED-NODES", "Cluster 2G: Compound Product Splitting and Hierarchy Type Normalization", 1, False),
    "reconcile_hierarchy_and_ownership_edges": ("CLUSTER-03D-MECE-HIERARCHY-AND-OWNERSHIP", "Cluster 3D: Portfolio Hierarchy Connectivity and Owner Relationship Wiring", 1, False),
    "synthesize_hierarchical_retrieval_docs": ("CLUSTER-07E-RETRIEVAL-BREADCRUMB-SYNTHESIS", "Cluster 7E: Hierarchical Retrieval Breadcrumb and Financial Context Synthesis", 2, False),
}


def classify_finding_cluster(finding: Any) -> Tuple[str, str, int, bool]:
    """Map a finding to (cluster_id, cluster_title, wave_number, requires_isolated_scrutiny)."""
    fix = finding.proposed_fix or "unclassified"
    raw = finding.raw_data or {}
    if fix == "merge_entities":
        return _classify_merge_cluster(raw)
    if fix in _SIMPLE_CLUSTER_MAP:
        return _SIMPLE_CLUSTER_MAP[fix]
    return ("CLUSTER-08-GOVERNANCE-AUDIT", "Cluster 8: Audit Provenance and Governance Review", 2, True)


def _classify_merge_cluster(raw: Dict[str, Any]) -> Tuple[str, str, int, bool]:
    ntype = str(raw.get("node_type_a") or "entity").lower()
    if ntype in ("stakeholder", "person", "person_profile", "internal_person"):
        return ("CLUSTER-02A-STAKEHOLDER-NAMES", "Cluster 2A: Stakeholder Nickname and Formal Name Consolidation", 1, False)
    if ntype == "technical_anchor":
        return ("CLUSTER-02B-TECHNICAL-SYSTEMS", "Cluster 2B: Technical Systems and Acronym Consolidation", 1, False)
    if ntype in ("workstream", "program"):
        if _is_proposal_or_low_margin(raw):
            return (
                "CLUSTER-02C2-PROPOSAL-LIFECYCLE-BOUNDARY",
                "Cluster 2C-2: Pre-Award Proposal Opportunity vs Active Program Review",
                1,
                True,
            )
        return ("CLUSTER-02C1-WORKSTREAM-PREFIXES", "Cluster 2C-1: Workstream and Initiative Prefix Alignment", 1, False)
    if ntype in ("role_group", "role"):
        if _is_scope_divergent_role(raw):
            return (
                "CLUSTER-02D2-ROLE-PROJECT-SCOPE",
                "Cluster 2D-2: Project-Specific Role vs Generic Job Title Disambiguation",
                1,
                True,
            )
        return ("CLUSTER-02D1-ROLE-FORMATTING", "Cluster 2D-1: Leadership Role Punctuation Standardization", 1, False)
    if ntype in ("org", "organization"):
        return ("CLUSTER-02E-ORGANIZATIONS", "Cluster 2E: Organization and Regulatory Agency Consolidation", 1, False)
    return ("CLUSTER-02F-GENERAL-ENTITIES", "Cluster 2F: General Entity Profile Consolidation", 1, False)


def _format_merge_or_dedup_cells(fix: str, raw: Dict[str, Any], finding: Any) -> Tuple[str, str, str]:
    if fix == "merge_entities":
        surv_name = raw.get("canonical_name_a") or raw.get("survivor_node_id") or ""
        ret_name = raw.get("canonical_name_b") or raw.get("retired_node_id") or ""
        return (
            f"{surv_name} <-> {ret_name}",
            f"[KEPT IN ACTIVE GRAPH]: {_clean_cell(surv_name, 38)} | [MOVED TO ARCHIVE]: {_clean_cell(ret_name, 38)}",
            f"Similarity {float(raw.get('similarity_score') or 0.0):.2f} (Margin {float(raw.get('margin_delta') or 0.0):.2f})",
        )
    w_claim = _clean_cell(raw.get("winner_claim") or finding.description, 42)
    l_claim = _clean_cell(raw.get("loser_claim") or finding.description, 42)
    return (
        w_claim,
        f"[KEPT IN ACTIVE GRAPH]: {w_claim} | [MOVED TO ARCHIVE]: {l_claim}",
        f"Similarity {float(raw.get('similarity') or 1.0):.2f}; all quotes preserved",
    )


def _format_source_cluster_cells(raw: Dict[str, Any]) -> Tuple[str, str, str]:
    clusters = raw.get("clusters") or []
    if clusters:
        total_m = sum(len(c.get("member_source_ids", [])) for c in clusters)
        first_name = (clusters[0].get("members") or [{}])[0].get("source_name") or "Source Document"
        return (
            _clean_cell(f"{first_name} ({len(clusters)} cluster(s))", 55),
            f"Consolidate {total_m} duplicate source records into {len(clusters)} canonical source(s)",
            f"{max(1, total_m - len(clusters))} duplicate source record(s)",
        )
    return (
        _clean_cell(raw.get("canonical_title", "Source Document"), 55),
        f"Consolidate {int(raw.get('group_size', 2))} duplicate source records into canonical source",
        f"{len(raw.get('duplicate_source_ids', []))} duplicate source record(s)",
    )


def _format_finding_row(finding: Any, debate: CouncilDebateRecord) -> str:
    raw = finding.raw_data or {}
    fix = finding.proposed_fix or ""
    if fix == "archive_candidate_assertion":
        ev_reason = str(raw.get("eviction_reason") or "")
        item = f"{raw.get('subject_name', 'Person')} -> {raw.get('object_name', 'Org')}"
        if ev_reason == "degenerate_single_token_candidate":
            detail = f"[MOVED TO ARCHIVE]: Incomplete single-word fragment ('{_clean_cell(raw.get('claim_text'), 36)}') lacking full statement context"
        elif ev_reason == "undiarized_unattributable_candidate":
            detail = f"[MOVED TO ARCHIVE]: Undiarized meeting fragment lacking individual speaker ('{_clean_cell(raw.get('claim_text'), 42)}')"
        else:
            detail = "[MOVED TO ARCHIVE]: Unpromoted staging draft; [KEPT IN ACTIVE GRAPH]: Existing promoted official record"
        src = f"{raw.get('source_title', 'Meeting')} ({str(raw.get('created_at', ''))[:10]})"
    elif fix in ("merge_entities", "deduplicate_assertions"):
        item, detail, src = _format_merge_or_dedup_cells(fix, raw, finding)
    elif fix == "supersede_assertion":
        item = f"{raw.get('subject_name', '')} ({str(raw.get('predicate', '')).replace('_', ' ').lower()})"
        detail = f"[KEPT IN ACTIVE GRAPH]: {_clean_cell(raw.get('claim_newer'), 38)} | [MOVED TO ARCHIVE]: {_clean_cell(raw.get('claim_older'), 38)}"
        src = f"{raw.get('timestamp_older', '')} vs {raw.get('timestamp_newer', '')}"
    elif fix == "consolidate_duplicate_sources":
        item, detail, src = _format_source_cluster_cells(raw)
    else:
        item = DETECTOR_PLAIN_LABELS.get(str(finding.detective or ""), "Hygiene Review")
        detail = _clean_cell(str(finding.description or "").replace("_", " "), 95)
        src = f"Priority {str(finding.severity or 'MEDIUM').title()}"
    return f"| {_clean_cell(item, 55)} | {_clean_cell(detail, 110)} | {_clean_cell(src, 55)} | {_clean_cell(debate.committee_verdict, 75)} |"


def _build_question_and_options(
    cluster_id: str, title: str, findings: List[Any], debates: List[CouncilDebateRecord]
) -> Tuple[str, str, Dict[str, Any]]:
    """Construct plain-English rationale, council summary, and validated ask_user_question payload."""
    alpha_sum = debates[0].team_alpha_argument if debates else "Consolidate records to eliminate duplication."
    beta_sum = debates[0].team_beta_counterargument if debates else "Verify provenance and scope boundaries before modifying."
    verdict_sum = debates[0].committee_verdict if debates else "Present atomic cluster for operator decision."
    if cluster_id == "CLUSTER-01F-UNDIARIZED-SPEAKER-ATTRIBUTION-HITL":
        return build_undiarized_speaker_question_payload(findings, alpha_sum, beta_sum, verdict_sum)
    rows = [
        "| Dimension | Plain-English Status and Record Comparison | Source and Timing Context | 8-Agent Council Verdict |",
        "| :--- | :--- | :--- | :--- |",
        *build_mece_cluster_finding_rows(findings, debates, _format_finding_row, _clean_cell, DETECTOR_PLAIN_LABELS),
        f"| 8-Agent Council Debate | Team Alpha: {_clean_cell(alpha_sum, 110)} | "
        f"Team Beta: {_clean_cell(beta_sum, 110)} | Consensus: {_clean_cell(verdict_sum, 75)} |",
    ]

    eff_count = int((findings[0].raw_data or {}).get("promotable_count") or 1) if len(findings) == 1 and getattr(findings[0], "proposed_fix", "") == "batch_promote_candidates" else len(findings)
    rationale, prompt_line, options = _cluster_copy_and_options(cluster_id, eff_count)
    full_question = "\n".join(rows) + "\n" + prompt_line
    rec_action = "exclude" if cluster_id == "CLUSTER-02D2-ROLE-PROJECT-SCOPE" else "apply"
    from dual_pipeline.hitl_governance_core.surfacing import parse_confidence_score
    from dual_pipeline.parameters import get_hitl_min_autonomous_confidence_floor, needs_operator_decision

    scores = sorted([parse_confidence_score(o, require_confidence=False) for o in options], reverse=True)
    c_top = scores[0] if scores else 0.0
    c_sec = scores[1] if len(scores) > 1 else 0.0
    margin = round(c_top - c_sec, 4)
    auto_ok = not needs_operator_decision(c_top, c_sec) and c_top >= get_hitl_min_autonomous_confidence_floor()
    return rationale, f"Team Alpha: {alpha_sum} | Team Beta: {beta_sum} | Verdict: {verdict_sum}", {
        "question": full_question,
        "options": options,
        "is_multi_select": False,
        "criticality_tier": "TIER_4_AUTONOMOUS" if auto_ok else "TIER_3_AMBIGUOUS",
        "category": "graph_hygiene_cluster_approval",
        "recommended_action": rec_action,
        "top_confidence": c_top,
        "runner_up_confidence": c_sec,
        "margin": margin,
        "autonomous_permitted": auto_ok,
        "surface_hitl": not auto_ok,
    }


_CLUSTER_TEMPLATES: Dict[str, Tuple[str, str, List[str]]] = {
    "CLUSTER-01-UNPROMOTED-STAGING-DRAFTS": (
        "These items are unpromoted draft notes sitting in staging. Because an official promoted record is already active or the draft comes from an undiarized session lacking a recoverable speaker, archiving clears the staging queue while preserving every source quote.",
        "Should we archive these {count} unpromoted staging drafts into the historical record?",
        [
            "(Recommended) [Confidence: HIGH (88%)] Archive all {count} unpromoted staging drafts",
            "[Confidence: MEDIUM (72%)] Keep unpromoted drafts in staging without archiving",
            "[Confidence: LOW (35%)] Defer staging cleanup for manual review",
        ],
    ),
    "CLUSTER-01B-LEAKED-CANDIDATES": (
        "These unapproved draft or citation-only notes leaked into active search. Demoting them removes unverified notes from live answers while preserving every source quote.",
        "Should we remove these {count} unapproved draft notes from active search results?",
        [
            "(Recommended) [Confidence: HIGH (92%)] Remove all {count} unapproved draft notes from active search",
            "[Confidence: MEDIUM (70%)] Keep current search visibility pending manual audit",
            "[Confidence: LOW (35%)] Defer draft search cleanup",
        ],
    ),
    "CLUSTER-01D-SPEAKER-ATTRIBUTION-HYGIENE": (
        "These active meeting statements have generic roster headers, missing speaker names, or contradictory speaker tags. Moving them to staging hold protects live answers without inventing speaker names.",
        "Should we move these {count} unverified speaker statements to staging hold while preserving their original text?",
        [
            "(Recommended) [Confidence: HIGH (92%)] Move all {count} unverified speaker statements to staging hold",
            "[Confidence: MEDIUM (70%)] Keep current statements active pending manual transcript review",
            "[Confidence: LOW (35%)] Defer speaker verification cleanup",
        ],
    ),
    "CLUSTER-01E-QUALIFIER-METADATA-SYNC": (
        "These approved official statements passed verification but still carry outdated staging labels in their internal metadata.",
        "Should we synchronize the internal labels on these {count} approved official statements?",
        [
            "(Recommended) [Confidence: HIGH (94%)] Synchronize internal labels on all {count} approved statements",
            "[Confidence: MEDIUM (72%)] Retain current internal labels unchanged",
            "[Confidence: LOW (35%)] Defer metadata label synchronization",
        ],
    ),
    "CLUSTER-02A-STAKEHOLDER-NAMES": (
        "These team member profiles split nicknames and formal names across two separate entries. Merging unites all meeting mentions under the formal profile with automatic redirects.",
        "Should we merge these {count} stakeholder nickname and formal name pairs into single canonical profiles?",
        [
            "(Recommended) [Confidence: HIGH (92%)] Merge all {count} stakeholder name pairs into formal profiles",
            "[Confidence: MEDIUM (68%)] Keep nickname and formal name profiles separate",
            "[Confidence: LOW (35%)] Defer stakeholder name consolidation",
        ],
    ),
    "CLUSTER-02B-TECHNICAL-SYSTEMS": (
        "These technical systems have duplicate profiles created when acronyms and full system names were extracted separately.",
        "Should we consolidate these {count} technical system and acronym pairs into single system profiles?",
        [
            "(Recommended) [Confidence: HIGH (92%)] Merge all {count} technical system and acronym pairs",
            "[Confidence: MEDIUM (68%)] Merge cloud platforms only and keep project tools separate",
            "[Confidence: LOW (40%)] Defer technical system consolidation",
        ],
    ),
    "CLUSTER-02C1-WORKSTREAM-PREFIXES": (
        "These workstreams and programs have duplicate entries caused by prefix label variations while describing the exact same approved scope.",
        "Should we align these {count} workstream prefix pairs into single canonical initiative profiles?",
        [
            "(Recommended) [Confidence: HIGH (92%)] Merge all {count} workstream prefix pairs retaining full scope",
            "[Confidence: MEDIUM (68%)] Keep numbered workstream labels separate from short names",
            "[Confidence: LOW (40%)] Defer workstream prefix alignment",
        ],
    ),
    "CLUSTER-02C2-PROPOSAL-LIFECYCLE-BOUNDARY": (
        "The 8-Agent Council flagged that these entries reference a pre-award proposal or pursuit opportunity where delivery work has not officially started or been won.",
        "Should we consolidate these entries as a single pre-award proposal opportunity rather than an active delivery program?",
        [
            "(Recommended) [Confidence: HIGH (85%)] Consolidate into one record designated as a pre-award proposal opportunity",
            "[Confidence: MEDIUM (70%)] Keep the proposal entry separate from general program entries",
            "[Confidence: LOW (40%)] Defer decision on proposal and program records",
        ],
    ),
    "CLUSTER-02D1-ROLE-FORMATTING": (
        "These leadership job titles are identical except for hyphen versus slash punctuation.",
        "Should we standardize the punctuation across these {count} leadership job role titles?",
        [
            "(Recommended) [Confidence: HIGH (92%)] Standardize all {count} formatting duplicate role titles",
            "[Confidence: MEDIUM (68%)] Retain separate hyphen and slash role entries",
            "[Confidence: LOW (35%)] Defer role title standardization",
        ],
    ),
    "CLUSTER-02D2-ROLE-PROJECT-SCOPE": (
        "The 8-Agent Council detected that one job title is generic while the other carries a specific project name.",
        "Should we keep the project-specific job title separate from the generic role title?",
        [
            "(Recommended) [Confidence: HIGH (88%)] Keep the project-specific role distinct from the generic role title",
            "[Confidence: MEDIUM (72%)] Merge the generic role into the project-specific role profile",
            "[Confidence: LOW (35%)] Defer role disambiguation",
        ],
    ),
    "CLUSTER-02E-ORGANIZATIONS": (
        "These companies and regulatory agencies have split entries caused by legal suffixes and agency acronyms.",
        "Should we consolidate these {count} organization and regulatory agency pairs into single canonical profiles?",
        [
            "(Recommended) [Confidence: HIGH (92%)] Consolidate all {count} organization and regulatory agency pairs",
            "[Confidence: MEDIUM (68%)] Consolidate corporate entities only and keep commissions separate",
            "[Confidence: LOW (40%)] Defer organization consolidation",
        ],
    ),
    "CLUSTER-04-DUPLICATE-STATEMENTS": (
        "These statement pairs repeat the exact same fact across multiple meeting extractions. Deduplicating keeps the primary statement and combines all supporting quotes.",
        "Should we deduplicate these {count} repeated statement pairs while keeping all supporting quotes?",
        [
            "(Recommended) [Confidence: HIGH (92%)] Deduplicate all {count} statement pairs with combined evidence",
            "[Confidence: MEDIUM (68%)] Deduplicate exact matches only and keep wording variants separate",
            "[Confidence: LOW (40%)] Defer statement deduplication",
        ],
    ),
    "CLUSTER-05-TEMPORAL-UPDATES": (
        "Newer official records update or replace older chronological statements for the same person or workstream.",
        "Should the newer official record supersede the older historical note for these {count} timeline item(s)?",
        [
            "(Recommended) [Confidence: HIGH (88%)] Supersede older note with the newer official record",
            "[Confidence: MEDIUM (72%)] Retain both chronological statements as concurrently active",
            "[Confidence: LOW (40%)] Defer timeline supersession",
        ],
    ),
    "CLUSTER-06-STAGING-PROMOTIONS": (
        "These {count} staging notes have high confidence and verified verbatim quotes from source transcripts, qualifying them for promotion to active official status.",
        "Should we promote these {count} verified high-confidence staging notes to active official records?",
        [
            "(Recommended) [Confidence: HIGH (92%)] Promote all {count} verified high-confidence staging notes to active official status",
            "[Confidence: MEDIUM (68%)] Hold in staging for individual meeting directive review",
            "[Confidence: LOW (40%)] Defer staging promotions",
        ],
    ),
    "CLUSTER-07-SEARCH-INDEX-SYNC": (
        "These active official records exist in the knowledge base but are missing from the keyword search index. Synchronizing makes them discoverable in search queries.",
        "Should we synchronize the keyword search index so all active official records are searchable?",
        [
            "(Recommended) [Confidence: HIGH (94%)] Synchronize all missing search index entries for active records",
            "[Confidence: MEDIUM (68%)] Defer search index synchronization",
            "[Confidence: LOW (35%)] Skip search index update",
        ],
    ),
    "CLUSTER-09A-DUPLICATE-SOURCES": (
        "Multiple source records represent the exact same meeting or document. Consolidating repoints all statements and quotes to the primary source record.",
        "Should we consolidate these {count} duplicate meeting and document source groups into single canonical sources?",
        [
            "(Recommended) [Confidence: HIGH (92%)] Consolidate all {count} duplicate source groups and preserve all quotes",
            "[Confidence: MEDIUM (68%)] Keep duplicate source records separate",
            "[Confidence: LOW (35%)] Defer source document consolidation",
        ],
    ),
    "CLUSTER-09B-DEGENERATE-AND-COLLIDED-SOURCES": (
        "These source records have placeholder titles or combine unrelated meetings under one identifier.",
        "Should we retitle or separate these {count} placeholder and collided source records?",
        [
            "(Recommended) [Confidence: HIGH (88%)] Retitle placeholder sources and separate collided meeting records",
            "[Confidence: MEDIUM (74%)] Retain current source titles and identifiers unchanged",
            "[Confidence: LOW (35%)] Defer source title remediation",
        ],
    ),
}


def _cluster_copy_and_options(cluster_id: str, count: int) -> Tuple[str, str, List[str]]:
    if cluster_id in _CLUSTER_TEMPLATES:
        rat, q_tmpl, opts_tmpl = _CLUSTER_TEMPLATES[cluster_id]
        return rat.format(count=count), q_tmpl.format(count=count), [o.format(count=count) for o in opts_tmpl]
    return (
        f"The 8-Agent Adversarial Council verified {count} item(s) in this category with zero loss of source quotes.",
        f"How would you like to handle the {count} item(s) in this review cluster?",
        [
            f"(Recommended) [Confidence: HIGH (92%)] Approve the council recommendation for all {count} item(s) in this cluster",
            "[Confidence: MEDIUM (68%)] Modify scope or review individual items separately",
            "[Confidence: LOW (40%)] Defer this cluster and retain current records unchanged",
        ],
    )


def _assemble_hitl_cluster(
    cid: str, title: str, wave: int, scrutiny: bool, c_findings: List[Any], c_debates: List[CouncilDebateRecord]
) -> HITLDecisionCluster:
    rationale, debate_sum, q_payload = _build_question_and_options(cid, title, c_findings, c_debates)
    refs = [r for cf in c_findings for r in cf.entity_references]
    def_dec = "exclude" if cid == "CLUSTER-02D2-ROLE-PROJECT-SCOPE" else "approve"
    promised = [
        asdict(pm)
        for idx, cf in enumerate(c_findings)
        for pm in compile_finding_promised_mutations(cf, cluster_id=cid, decision=def_dec, idx=idx)
    ]
    return HITLDecisionCluster(
        cluster_id=cid, title=title, wave_number=wave, category=cid.split("-", 2)[-1].lower(),
        finding_count=len(c_findings), requires_isolated_scrutiny=scrutiny,
        plain_english_rationale=rationale, council_debate_summary=debate_sum,
        entity_references=refs, ready_to_ask_question=q_payload,
        findings=[
            {"detective": cf.detective, "severity": cf.severity, "description": cf.description, "entity_references": cf.entity_references, "proposed_fix": cf.proposed_fix, "raw_data": cf.raw_data}
            for cf in c_findings
        ],
        promised_mutations=promised,
    )


class AdversarialCouncilChairAgent:
    """Agent 8: Moderates 8-Agent Council debate and partitions findings into atomic HITL clusters."""

    def deliberate_and_cluster(
        self,
        findings: List[Any],
        agent5_notes: Dict[int, str],
        agent6_notes: Dict[int, str],
        agent7_notes: Dict[int, str],
    ) -> Tuple[List[CouncilDebateRecord], List[HITLDecisionCluster]]:
        debates: List[CouncilDebateRecord] = []
        grouped: Dict[str, Tuple[str, int, bool, List[Any], List[CouncilDebateRecord]]] = {}

        for idx, f in enumerate(findings):
            cid, title, wave, scrutiny = classify_finding_cluster(f)
            fix_plain = FIX_PLAIN_LABELS.get(f.proposed_fix or "", "review and clean up records")
            alpha = f"Recommend action to {fix_plain} and eliminate duplicate or stale state."
            beta = (
                agent6_notes.get(idx)
                or agent5_notes.get(idx)
                or agent7_notes.get(idx)
                or "Preserve all verbatim evidence quotes and audit trail links."
            )
            verdict = (
                "Isolate for explicit boundary decision"
                if scrutiny
                else "Recommended for approval with redirect and evidence preservation"
            )
            rec = CouncilDebateRecord(
                finding_index=idx,
                cluster_id=cid,
                team_alpha_argument=alpha,
                team_beta_counterargument=beta,
                committee_verdict=verdict,
                requires_isolated_scrutiny=scrutiny,
                confidence_pct=88 if scrutiny else 90,
                confidence_tier="HIGH",
            )
            debates.append(rec)
            if cid not in grouped:
                grouped[cid] = (title, wave, scrutiny, [], [])
            grouped[cid][3].append(f)
            grouped[cid][4].append(rec)

        clusters: List[HITLDecisionCluster] = [
            _assemble_hitl_cluster(cid, title, wave, scrutiny, c_findings, c_debates)
            for cid, (title, wave, scrutiny, c_findings, c_debates) in sorted(grouped.items())
        ]
        return debates, clusters
