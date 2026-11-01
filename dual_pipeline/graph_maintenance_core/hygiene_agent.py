"""Knowledge Graph Hygiene Agent - 13-Agent Adversarial Council & Panel Orchestration."""

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dual_pipeline.graph_scout import GraphScout

from .health import calculate_graph_health_metrics
from .hygiene_council import (  # noqa: lazy-decouple
    AdversarialCouncilChairAgent, EntityDisambiguationCriticAgent,
    LifecycleBoundaryAdvocateAgent, RetrievalSidecarParityAgent, load_active_boundary_exclusions,
)
from .hygiene_mutation_agents import (  # noqa: lazy-decouple
    GraphRetrievalAndFtsParityAgent, HitlContractToMutationFidelityAgent,
    IntraTxMutationOrderAndTriggerAgent, ItemizedPhysicalProofVerifierAgent,
    SidecarVectorAndPromotionParityAgent, normalize_merge_finding_survivors,
)

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    detective: str
    severity: str
    description: str
    entity_references: List[str]
    evidence_missing: bool = False
    proposed_fix: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None


@dataclass
class RemediationPackage:
    status: str
    findings: List[Finding]
    remediation_directives: List[Dict[str, Any]]
    approval_required: bool = True
    auto_apply: bool = False
    model_tier: str = "FRONTIER_HIGH_REASONING"
    council_size: int = 13
    council_debates: List[Dict[str, Any]] = field(default_factory=list)
    hitl_clusters: List[Dict[str, Any]] = field(default_factory=list)


def _resolve_clutter_fix(item: Any, details: Dict[str, Any]) -> Optional[str]:
    if item.finding_type == "orphan_node":
        is_cand = str(details.get("status") or "").lower() == "candidate" and details.get("created_at")
        if int(details.get("edge_count") or 0) > 0 or (is_cand and not details.get("allow_aged_candidate_orphan")):
            return None
        return "archive_node"
    if item.finding_type == "revalidation_review_90d":
        return "escalate_90d_revalidation"
    return "archive_candidate_assertion" if details.get("recommended_option", "ARCHIVE") == "ARCHIVE" else None


def _audit_bloat_and_clutter(health: Dict[str, Any], scout: GraphScout, conn: Any) -> List[Finding]:
    findings: List[Finding] = []
    orphaned_ev = int((health.get("counts") or {}).get("orphaned_evidence_records") or 0)
    bloat_ratio = float(health.get("bloat_ratio") or health.get("max_bloat_ratio", 1.0))
    if float(health.get("bloat_ratio") or 0.0) > 1.5 or (bloat_ratio > 1.5 and orphaned_ev > 0):
        findings.append(
            Finding(
                detective="StructuralValidator",
                severity="HIGH",
                description=f"Graph bloat ratio is {bloat_ratio:.2f}, exceeding 1.5x threshold.",
                entity_references=[],
                proposed_fix="run_graph_gc",
                raw_data={"bloat_ratio": bloat_ratio, "orphaned_evidence_records": orphaned_ev},
            )
        )
    for item in scout.scan_orphan_and_clutter(conn):
        details = item.details or {}
        if fix := _resolve_clutter_fix(item, details):
            findings.append(
                Finding(
                    detective="StructuralValidator",
                    severity="MEDIUM",
                    description=f"Clutter detected: {item.target_id} ({item.finding_type}): {item.label}",
                    entity_references=[item.target_id],
                    proposed_fix=fix,
                    raw_data=details,
                )
            )
    return findings


def _audit_duplicate_entities(scout: GraphScout, conn: Any) -> List[Finding]:
    findings: List[Finding] = []
    for dup in scout.scan_duplicate_entities(conn):
        conf_tier = "HIGH (90%)" if dup.confidence_merge >= 0.85 else f"MEDIUM ({int(dup.confidence_merge*100)}%)"
        findings.append(
            Finding(
                detective="StructuralValidator",
                severity="HIGH",
                description=f"Duplicate entity pair: {dup.canonical_name_a} ({dup.node_id_a}) <-> {dup.canonical_name_b} ({dup.node_id_b}) [similarity={dup.similarity_score:.2f}]",
                entity_references=[dup.node_id_a, dup.node_id_b],
                proposed_fix="merge_entities",
                raw_data={
                    "node_id_a": dup.node_id_a, "canonical_name_a": dup.canonical_name_a,
                    "node_type_a": dup.node_type_a, "description_a": dup.description_a,
                    "node_id_b": dup.node_id_b, "canonical_name_b": dup.canonical_name_b,
                    "node_type_b": dup.node_type_b, "description_b": dup.description_b,
                    "similarity_score": dup.similarity_score, "confidence_merge": dup.confidence_merge,
                    "confidence_tier": conf_tier, "margin_delta": dup.margin_delta,
                    "recommended_option": f"Merge '{dup.canonical_name_b}' into '{dup.canonical_name_a}'",
                    "rationale": dup.rationale,
                },
            )
        )
    normalize_merge_finding_survivors(conn, findings)
    return findings


def _audit_edge_parity(scout: GraphScout, conn: Any) -> List[Finding]:
    findings: List[Finding] = []
    edge_parity = scout.scan_edge_assertion_parity(conn) if hasattr(scout, "scan_edge_assertion_parity") else []
    for ep in edge_parity:
        if ep.issue_type == "unmapped_auto_linkable":
            fix = "auto_link_edge"
            sev = "MEDIUM"
        elif ep.issue_type == "dangling_endpoint":
            fix = "archive_edge"
            sev = "HIGH"
        else:
            fix = "synthesize_edge_assertion"
            sev = "MEDIUM"

        findings.append(
            Finding(
                detective="StructuralValidator",
                severity=sev,
                description=f"Edge parity issue ({ep.issue_type}): {ep.description}",
                entity_references=[ep.edge_id, ep.from_node_id, ep.to_node_id],
                proposed_fix=fix,
                raw_data=ep.details,
            )
        )
    return findings


def _audit_duplicate_assertions(health: Dict[str, Any]) -> List[Finding]:
    findings: List[Finding] = []
    proposals = health.get("duplicate_proposals") or health.get("duplicate_proposals_sample", [])
    for dup_prop in proposals:
        findings.append(
            Finding(
                detective="StructuralValidator",
                severity="MEDIUM",
                description=f"Semantic duplicate assertion pair: {dup_prop.get('loser_id')} superseded by {dup_prop.get('winner_id')} (similarity={dup_prop.get('similarity', 0.0):.2f})",
                entity_references=[dup_prop.get("loser_id", ""), dup_prop.get("winner_id", "")],
                proposed_fix="deduplicate_assertions",
                raw_data=dup_prop,
            )
        )
    return findings


class StructuralValidatorAgent:
    """Checks for node/edge schema compliance, orphaned nodes, and duplicate entities."""

    def audit(self, graph_path: Path, conn: Any) -> List[Finding]:
        from ..graph_scout_core.hierarchy_mece_scans import audit_mece_hierarchy_findings
        logger.info("[Agent 1] StructuralValidatorAgent scanning topology...")
        health = calculate_graph_health_metrics(conn)
        scout = GraphScout(graph_path=graph_path)
        findings: List[Finding] = []
        findings.extend(_audit_bloat_and_clutter(health, scout, conn))
        findings.extend(_audit_duplicate_entities(scout, conn))
        findings.extend(_audit_edge_parity(scout, conn))
        findings.extend(_audit_duplicate_assertions(health))
        findings.extend(audit_mece_hierarchy_findings(conn, Finding))
        return findings


class TemporalAuditorAgent:
    """Checks for contradictions in W3 temporal attributes (observed_at, stale_after)."""

    def audit(self, graph_path: Path, conn: Any) -> List[Finding]:
        logger.info("[Agent 2] TemporalAuditorAgent scanning temporal windows...")
        findings = []

        scout = GraphScout(graph_path=graph_path)
        contradictions = scout.scan_temporal_contradictions(conn)
        for c in contradictions:
            findings.append(
                Finding(
                    detective="TemporalAuditor",
                    severity="HIGH",
                    description=f"Temporal contradiction in {c.subject_name or c.subject_node_id}: {c.rationale}",
                    entity_references=[c.assertion_id_older, c.assertion_id_newer],
                    proposed_fix="supersede_assertion",
                    raw_data={
                        "subject_node_id": c.subject_node_id,
                        "subject_name": c.subject_name,
                        "predicate": c.predicate,
                        "assertion_id_older": c.assertion_id_older,
                        "assertion_id_newer": c.assertion_id_newer,
                        "claim_older": c.claim_older,
                        "claim_newer": c.claim_newer,
                        "scope_text_older": c.scope_text_older,
                        "scope_text_newer": c.scope_text_newer,
                        "evidence_quote_older": c.evidence_quote_older,
                        "evidence_quote_newer": c.evidence_quote_newer,
                        "source_title_older": c.source_title_older,
                        "source_title_newer": c.source_title_newer,
                        "timestamp_older": c.timestamp_older,
                        "timestamp_newer": c.timestamp_newer,
                        "confidence_supersede": c.confidence_supersede,
                        "confidence_coexist": c.confidence_coexist,
                        "confidence_tier": "HIGH (85%)" if c.confidence_supersede >= 0.85 else f"MEDIUM ({int(c.confidence_supersede*100)}%)",
                        "margin_delta": c.margin_delta,
                        "recommended_option": f"Supersede older assertion '{c.claim_older}' with newer '{c.claim_newer}'",
                    },
                )
            )

        return findings


def _audit_leaked_search_docs(cursor: Any) -> Optional[Finding]:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='search_document'"
    )
    if not cursor.fetchone():
        return None
    cursor.execute(
        """SELECT search_document_id
             FROM search_document
            WHERE retrieval_eligible = 1
              AND currentness_state IN ('historical', 'superseded')"""
    )
    leaked_ids = [r[0] for r in cursor.fetchall()]
    if not leaked_ids:
        return None
    return Finding(
        detective="ProvenanceInspector",
        severity="MEDIUM",
        description=f"Found {len(leaked_ids)} active search documents with historical/superseded state requiring deactivation.",
        entity_references=leaked_ids,
        proposed_fix="deactivate_search_documents",
        raw_data={"leaked_document_ids": leaked_ids},
    )


def _audit_ungrounded_assertions(cursor: Any) -> Optional[Finding]:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assertion_evidence'")
    if not cursor.fetchone():
        return None
    asrt_cols = {r[1] for r in cursor.execute("PRAGMA table_info(assertion)").fetchall()}
    ev_cols = {r[1] for r in cursor.execute("PRAGMA table_info(assertion_evidence)").fetchall()}
    asrt_id = "assertion_id" if "assertion_id" in asrt_cols else ("id" if "id" in asrt_cols else None)
    ev_asrt_id = "assertion_id" if "assertion_id" in ev_cols else ("id" if "id" in ev_cols else None)
    if not asrt_id or not ev_asrt_id:
        return None
    where_clauses = []
    if "currentness_state" in asrt_cols:
        where_clauses.append("a.currentness_state = 'current'")
    if "canonical_status" in asrt_cols:
        where_clauses.append("a.canonical_status = 'canonical'")
    where_clauses.append(f"a.{asrt_id} NOT IN (SELECT DISTINCT {ev_asrt_id} FROM assertion_evidence WHERE {ev_asrt_id} IS NOT NULL)")
    where_sql = " AND ".join(where_clauses)
    s_col = "a.subject_node_id" if "subject_node_id" in asrt_cols else ("a.entity_id" if "entity_id" in asrt_cols else "NULL as subject_node_id")
    p_col = "a.predicate" if "predicate" in asrt_cols else "NULL as predicate"
    ungrounded_sql = "SELECT a." + asrt_id + " AS assertion_id, " + s_col + ", " + p_col + " FROM assertion a WHERE " + where_sql
    cursor.execute(ungrounded_sql)
    ungrounded = [dict(r) if hasattr(r, "keys") else {"assertion_id": r[0]} for r in cursor.fetchall()]
    if not ungrounded:
        return None
    return Finding(
        detective="ProvenanceInspector", severity="HIGH",
        description=f"Detected {len(ungrounded)} active canonical assertion(s) missing evidence grounding.",
        entity_references=[u["assertion_id"] for u in ungrounded], evidence_missing=True, proposed_fix="reconcile_evidence",
        raw_data={"ungrounded_assertions": ungrounded, "ungrounded_assertion_ids": [u["assertion_id"] for u in ungrounded]},
    )


def _audit_promotable_candidates(
    cursor: Any, asrt_cols: set[str], excluded_ids: Optional[set[str]] = None
) -> Optional[Finding]:
    from .speaker_hygiene_rules import audit_safe_promotable_candidates
    return audit_safe_promotable_candidates(cursor, asrt_cols, excluded_ids, Finding)


def _audit_unpromotable_candidates(
    cursor: Any, asrt_cols: set[str], excluded_ids: Optional[set[str]] = None
) -> List[Finding]:
    from .speaker_hygiene_rules import audit_unpromotable_staging_candidates
    return audit_unpromotable_staging_candidates(cursor, asrt_cols, excluded_ids, Finding)


def _audit_sidecar_drift(cursor: Any, asrt_cols: set[str], all_tables: set[str]) -> Optional[Finding]:
    if "search_document" not in all_tables or "canonical_status" not in asrt_cols:
        return None
    sd_cols = {r[1] for r in cursor.execute("PRAGMA table_info(search_document)").fetchall()}
    retrieval_clause = "a.retrieval_eligible = 1 AND " if "retrieval_eligible" in asrt_cols else ""
    curr_clause = "a.currentness_state = 'current' AND " if "currentness_state" in asrt_cols else ""
    covered_union = "SELECT DISTINCT search_document_id FROM search_document WHERE search_document_id IS NOT NULL"
    if "assertion_id" in sd_cols:
        covered_union += " UNION SELECT DISTINCT assertion_id FROM search_document WHERE assertion_id IS NOT NULL"
    cursor.execute(
        f"""SELECT a.assertion_id
             FROM assertion a
            WHERE {retrieval_clause}{curr_clause}a.canonical_status = 'canonical'
              AND a.assertion_id NOT IN ({covered_union})"""
    )
    missing_sidecar = [r[0] for r in cursor.fetchall()]
    if not missing_sidecar:
        return None
    return Finding(
        detective="ProvenanceInspector",
        severity="MEDIUM",
        description=f"Detected {len(missing_sidecar)} active canonical assertion(s) missing from search_document sidecar.",
        entity_references=missing_sidecar,
        proposed_fix="resync_search_sidecars",
        raw_data={"missing_search_document_ids": missing_sidecar},
    )


class ProvenanceInspectorAgent:
    """Verifies that assertions have valid source provenance and runtime evidence."""

    def audit(self, graph_path: Path, conn: Any) -> List[Finding]:
        from ..graph_scout_core.antipattern_scans import scan_retrieval_antipatterns as _scan_ap  # noqa: lazy-decouple
        from ..graph_scout_core.source_scans import audit_source_hygiene_findings
        from .speaker_hygiene_rules import build_antipattern_hygiene_findings
        logger.info("[Agent 3] ProvenanceInspectorAgent verifying evidence grounding...")
        findings = []
        cursor = conn.cursor()
        f_doc = _audit_leaked_search_docs(cursor)
        if f_doc:
            findings.append(f_doc)
        findings.extend(audit_source_hygiene_findings(conn, Finding))
        findings.extend(build_antipattern_hygiene_findings(_scan_ap(conn), Finding, cursor=cursor))
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assertion'")
        if not cursor.fetchone():
            return findings

        f_ungrounded = _audit_ungrounded_assertions(cursor)
        if f_ungrounded:
            findings.append(f_ungrounded)

        asrt_cols = {r[1] for r in cursor.execute("PRAGMA table_info(assertion)").fetchall()}
        claimed_cands = {
            r for f in findings
            if f.proposed_fix in (
                "demote_leaked_candidate_assertion",
                "archive_candidate_assertion",
                "quarantine_undiarized_or_misattributed_claim",
                "rebind_placeholder_speaker",
                "attribute_undiarized_speaker_hitl",
            )
            for r in f.entity_references
        }
        f_promotable = _audit_promotable_candidates(cursor, asrt_cols, claimed_cands)
        if f_promotable:
            findings.append(f_promotable)
            claimed_cands.update(f_promotable.entity_references)
        findings.extend(_audit_unpromotable_candidates(cursor, asrt_cols, claimed_cands))

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        all_tables = {r[0] for r in cursor.fetchall()}
        f_sidecar = _audit_sidecar_drift(cursor, asrt_cols, all_tables)
        if f_sidecar:
            findings.append(f_sidecar)
        from ..graph_scout_core.hierarchy_mece_scans import audit_retrieval_breadcrumb_findings  # noqa: lazy-decouple
        findings.extend(audit_retrieval_breadcrumb_findings(conn, Finding))
        return findings


def _audit_operator_signatures(cursor: Any, cols: set[str]) -> List[Finding]:
    col = "id" if "id" in cols else "audit_event_id"
    sel_sql = f"SELECT {col} FROM audit_event WHERE operator_signature IS NULL OR operator_signature = ''"
    cursor.execute(sel_sql)
    return [
        Finding(
            detective="RedTeamer", severity="HIGH",
            description=f"Unauthenticated audit event detected: {r[0]} without signature.",
            entity_references=[r[0]], proposed_fix="quarantine_audit_record", raw_data={"audit_event_id": r[0]},
        )
        for r in cursor.fetchall()
    ]


def _audit_approval_references(cursor: Any, cols: set[str]) -> List[Finding]:
    id_col = "audit_event_id" if "audit_event_id" in cols else "id"
    where = [f"({id_col} IS NOT NULL AND {id_col} != '')", "(approval_reference IS NULL OR approval_reference = '')"]
    if "migration_or_ingestion_run_id" in cols:
        where.append("(migration_or_ingestion_run_id IS NULL OR migration_or_ingestion_run_id = '')")
    if "validation_reference" in cols:
        where.append("(validation_reference IS NULL OR validation_reference = '')")

    query = "SELECT " + id_col + ", action, actor_or_process FROM audit_event WHERE " + " AND ".join(where)
    cursor.execute(query)
    return [
        Finding(
            detective="RedTeamer",
            severity="HIGH",
            description=f"Unauthenticated mutation event detected: {r[0]} (action={r[1] if len(r)>1 else 'unknown'}) lacking approval and ingestion provenance.",
            entity_references=[r[0]],
            proposed_fix="review_audit_record",
            raw_data={"audit_event_id": r[0], "action": r[1] if len(r)>1 else "unknown"},
        )
        for r in cursor.fetchall()
    ]


class RedTeamerAgent:
    """Attempts to find logical flaws, unauthenticated approval receipts, or bypasses."""

    def audit(self, graph_path: Path, conn: Any) -> List[Finding]:
        logger.info("[Agent 4] RedTeamerAgent searching for bypasses and logic gaps...")
        cursor = conn.cursor()
        findings: List[Finding] = []
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_event'")
        if not cursor.fetchone():
            return findings
        cursor.execute("PRAGMA table_info(audit_event)")
        cols = {row[1] for row in cursor.fetchall()}
        if "operator_signature" in cols:
            findings.extend(_audit_operator_signatures(cursor, cols))
        if "approval_reference" in cols:
            findings.extend(_audit_approval_references(cursor, cols))
        return findings


def _veto_archive_node(finding: Finding, raw: Dict[str, Any], merge_target_ids: set[str]) -> bool:
    if any(nid in merge_target_ids for nid in finding.entity_references):
        return True
    if int(raw.get("edge_count") or 0) > 0:
        return True
    return bool(
        str(raw.get("status") or "").lower() == "candidate"
        and raw.get("created_at")
        and not raw.get("allow_aged_candidate_orphan")
    )


def _veto_merge_entities(raw: Dict[str, Any]) -> bool:
    from dual_pipeline.graph_scout_core.matching import (
        has_conflicting_qualifiers,
        has_description_conflict,
    )

    nid_a = str(raw.get("node_id_a") or "")
    nid_b = str(raw.get("node_id_b") or "")
    if nid_a.startswith("unresolved_") or nid_b.startswith("unresolved_"):
        return True
    if has_conflicting_qualifiers(str(raw.get("canonical_name_a") or ""), str(raw.get("canonical_name_b") or "")):
        return True
    return has_description_conflict(str(raw.get("description_a") or ""), str(raw.get("description_b") or ""))


def _is_false_positive_finding(finding: Finding, merge_target_ids: set[str]) -> bool:
    """Return True if a finding violates retirement or entity-disambiguation guardrails."""
    from dual_pipeline.graph_scout_core.matching import has_conflicting_qualifiers

    raw = finding.raw_data or {}
    fix = finding.proposed_fix or ""
    if fix == "archive_node":
        return _veto_archive_node(finding, raw, merge_target_ids)
    if fix == "archive_candidate_assertion":
        reason = str(raw.get("eviction_reason") or "")
        allowed_reasons = {
            "superseded_by_newer_or_canonical_assertion",
            "degenerate_single_token_candidate",
            "undiarized_unattributable_candidate",
        }
        return bool(reason not in allowed_reasons and (raw.get("evidence_quote") or raw.get("linked_evidence_id")))
    if fix == "merge_entities":
        return _veto_merge_entities(raw)
    if fix == "deduplicate_assertions":
        return has_conflicting_qualifiers(str(raw.get("winner_claim") or ""), str(raw.get("loser_claim") or ""))
    return False


def _filter_and_vet_findings(
    findings: List[Finding],
    merge_targets: set[str],
    excluded_pairs: Optional[set[Tuple[str, str]]] = None,
) -> Tuple[List[Finding], List[Dict[str, Any]]]:
    vetted: List[Finding] = []
    directives: List[Dict[str, Any]] = []
    seen: set = set(excluded_pairs or set())
    for f in findings:
        if _is_false_positive_finding(f, merge_targets):
            continue
        if not f.proposed_fix:
            vetted.append(f)
            continue
        if f.proposed_fix == "merge_entities" and len(f.entity_references) == 2:
            pair = tuple(sorted(f.entity_references))
            if pair in seen:
                continue
            seen.add(pair)
        elif (f.proposed_fix, "::".join(sorted(str(r) for r in f.entity_references if r)) or f.proposed_fix) in seen:
            continue
        vetted.append(f)
        directives.append({
            "action": "remediate",
            "target": f.entity_references,
            "fix": f.proposed_fix,
            "detective_source": f.detective,
            "severity": f.severity,
            "raw_data": f.raw_data,
        })
    return vetted, directives


class SynthesisJudgeAgent:
    """Synthesizes findings, runs 13-Agent Council debate, and constructs HITL clusters."""

    def __init__(self) -> None:
        self.agent_5_critic = EntityDisambiguationCriticAgent()
        self.agent_6_lifecycle = LifecycleBoundaryAdvocateAgent()
        self.agent_7_parity = RetrievalSidecarParityAgent()
        self.agent_8_chair = AdversarialCouncilChairAgent()
        self.agent_9_fidelity = HitlContractToMutationFidelityAgent()
        self.agent_10_tx_order = IntraTxMutationOrderAndTriggerAgent()
        self.agent_11_proof = ItemizedPhysicalProofVerifierAgent()
        self.agent_12_retrieval = GraphRetrievalAndFtsParityAgent()
        self.agent_13_sidecar = SidecarVectorAndPromotionParityAgent()

    def synthesize(
        self,
        findings: List[Finding],
        query_impacts: Optional[Sequence[Any]] = None,
        excluded_pairs: Optional[set[Tuple[str, str]]] = None,
        conn: Optional[Any] = None,
    ) -> RemediationPackage:
        logger.info(f"[Agents 5-13 & Presiding Judge] Deliberating across {len(findings)} findings...")
        self.agent_9_fidelity.review(findings, conn=conn)
        merge_targets = {
            e for f in findings
            if f.proposed_fix in ("merge_entities", "split_or_normalize_compound_node", "reconcile_hierarchy_and_ownership_edges")
            for e in f.entity_references
        }
        vetted, directives = _filter_and_vet_findings(findings, merge_targets, excluded_pairs=excluded_pairs)

        if query_impacts:
            imap = {
                str(getattr(q, "entity_id", "")).strip().lower(): getattr(q, "failure_rate", 0.0)
                + (getattr(q, "query_frequency", 0) / 100.0)
                for q in query_impacts
            }
            vetted.sort(key=lambda f: max([imap.get(str(e).strip().lower(), 0.0) for e in f.entity_references] or [0.0]), reverse=True)
            directives.sort(key=lambda d: max([imap.get(str(e).strip().lower(), 0.0) for e in d.get("target", [])] or [0.0]), reverse=True)

        a5 = self.agent_5_critic.review(vetted)
        a6 = self.agent_6_lifecycle.review(vetted)
        a7 = self.agent_7_parity.review(vetted)
        debates, clusters = self.agent_8_chair.deliberate_and_cluster(vetted, a5, a6, a7)

        return RemediationPackage(
            status="proposed",
            findings=vetted,
            remediation_directives=directives,
            approval_required=True,
            auto_apply=False,
            model_tier="FRONTIER_HIGH_REASONING",
            council_size=13,
            council_debates=[asdict(d) for d in debates],
            hitl_clusters=[asdict(c) for c in clusters],
        )


class GraphHygienePanel:
    """
    Orchestrates the 13-Agent Adversarial Council & Presiding Judge Committee.
    """

    def __init__(self, conn: Optional[Any] = None, graph_path: Optional[Path] = None):
        self.conn = conn
        self.graph_path = graph_path
        self.detective_1 = StructuralValidatorAgent()
        self.detective_2 = TemporalAuditorAgent()
        self.detective_3 = ProvenanceInspectorAgent()
        self.detective_4 = RedTeamerAgent()
        self.judge = SynthesisJudgeAgent()
        self.agent_5_critic = self.judge.agent_5_critic
        self.agent_6_lifecycle = self.judge.agent_6_lifecycle
        self.agent_7_parity = self.judge.agent_7_parity
        self.agent_8_chair = self.judge.agent_8_chair
        self.agent_9_fidelity = self.judge.agent_9_fidelity
        self.agent_10_tx_order = self.judge.agent_10_tx_order
        self.agent_11_proof = self.judge.agent_11_proof
        self.agent_12_retrieval = self.judge.agent_12_retrieval
        self.agent_13_sidecar = self.judge.agent_13_sidecar

    def _collect_findings(self, graph_path: Path, conn: Any) -> List[Finding]:
        findings = []
        for det in (self.detective_1, self.detective_2, self.detective_3, self.detective_4):
            findings.extend(det.audit(graph_path, conn))
        return findings

    def run_full_audit(
        self,
        target_graph: Optional[Path] = None,
        conn: Optional[Any] = None,
        query_impacts: Optional[Sequence[Any]] = None,
        answer_ledger_path: Optional[Path | str] = None,
    ) -> RemediationPackage:
        active_graph = target_graph or self.graph_path or Path("graph.db")
        active_conn = conn or self.conn

        if not query_impacts and answer_ledger_path:
            from .harm_prioritized_planner import extract_query_impact_records_from_ledger
            query_impacts = extract_query_impact_records_from_ledger(answer_ledger_path)

        from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
        if active_conn is not None:
            if active_conn.row_factory is None:
                active_conn.row_factory = Row
            all_findings = self._collect_findings(active_graph, active_conn)
            excluded_pairs = load_active_boundary_exclusions(active_conn)
            return self.judge.synthesize(all_findings, query_impacts=query_impacts, excluded_pairs=excluded_pairs, conn=active_conn)
        with GovernedStorageContext.read_context(active_graph) as c:
            if c.row_factory is None:
                c.row_factory = Row
            all_findings = self._collect_findings(active_graph, c)
            excluded_pairs = load_active_boundary_exclusions(c)
            return self.judge.synthesize(all_findings, query_impacts=query_impacts, excluded_pairs=excluded_pairs, conn=c)

    def run_panel(self, graph_path: Path) -> RemediationPackage:
        return self.run_full_audit(target_graph=graph_path)


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        sys.exit("Usage: python -m dual_pipeline.graph_maintenance_core.hygiene_agent <graph_path.json>")
    pkg = GraphHygienePanel().run_panel(Path(sys.argv[1]))
    print(f"Panel execution complete. Findings generated: {len(pkg.findings)}")
    print(json.dumps([f.__dict__ for f in pkg.findings], indent=2))
