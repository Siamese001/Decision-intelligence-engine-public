"""
dual_pipeline/llm_judge_core/adversarial_probe.py

Single-Pass Bounded Adversarial Auditor, Prosecutorial Probe, and Gate C3 Stratified Counterfactual Recall Probe.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.storage.governed_context import GovernedStorageContext

logger = logging.getLogger(__name__)

PROHIBITED_C19_PATTERNS = [
    re.compile(r"\binternal_advisory\b", re.IGNORECASE),
    re.compile(r"\bcareer\s+(?:strategy|leverage|capital|positioning)\b", re.IGNORECASE),
    re.compile(r"\bpersonal\s+(?:brand|positioning|advancement)\b", re.IGNORECASE),
    re.compile(r"\bpolitical\s+maneuvering\b", re.IGNORECASE),
    re.compile(r"\bcompensation\s+negotiation\b", re.IGNORECASE),
    re.compile(r"\bnetworking\s+wedge\b", re.IGNORECASE),
]

EPISTEMIC_INFLATION_PATTERNS = [
    re.compile(r"\bguaranteed\s+success\b", re.IGNORECASE),
    re.compile(r"\bzero\s+risk\b", re.IGNORECASE),
    re.compile(r"\bunquestionably\s+proven\b", re.IGNORECASE),
]

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"\b(?:ignore\s+(?:all\s+)?(?:previous\s+)?instructions?|system\s+(?:prompt\s+)?override)\b", re.IGNORECASE),
    re.compile(r"\b(?:you\s+are\s+now\s+in\s+test\s+mode|mode:\s*bypass|override\s+scoring)\b", re.IGNORECASE),
    re.compile(r"\b(?:force[_\s]+verdict:\s*pass|award\s+100\s+points|score\s+must\s+be\s+100)\b", re.IGNORECASE),
]


@dataclass
class AdversarialAuditResult:
    """Result of the bounded prosecutorial audit across Outputs 1, 2, and 3."""

    status: str  # "CLEARED", "ADVISORY_DEFECTS", "VETO_TRIGGERED"
    hard_veto: bool = False
    c19_breach_detected: bool = False
    prompt_injection_detected: bool = False
    ungrounded_entities: List[str] = field(default_factory=list)
    epistemic_warnings: List[str] = field(default_factory=list)
    adversarial_score_penalty: int = 0
    indictment_summary: str = ""
    remediation_recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "hard_veto": self.hard_veto,
            "c19_breach_detected": self.c19_breach_detected,
            "prompt_injection_detected": self.prompt_injection_detected,
            "ungrounded_entities": self.ungrounded_entities,
            "epistemic_warnings": self.epistemic_warnings,
            "adversarial_score_penalty": self.adversarial_score_penalty,
            "indictment_summary": self.indictment_summary,
            "remediation_recommendations": self.remediation_recommendations,
        }


def _is_single_entity_grounded(ent: Dict[str, Any], f1_lower: str, f1_lower_norm: str) -> bool:
    eid = str(ent.get("id") or "")
    name_part = eid.split(":")[-1].replace("_", " ").replace("-", " ") if ":" in eid else eid
    if len(name_part) <= 2 or name_part.lower() in f1_lower:
        return True
    ent_name = str(ent.get("name") or "").lower()
    if ent_name and (ent_name in f1_lower or ent_name.replace(" & ", " and ") in f1_lower_norm):
        return True
    toks = [t for t in re.findall(r"[a-z0-9]{4,}", f"{name_part.lower()} {ent_name}") if t not in {"system", "role", "workstream", "team"}]
    return bool(toks and any(t in f1_lower for t in toks))


class BoundedAdversarialProbe:
    """Executes targeted prosecutorial inspection across candidate outputs."""

    def audit(
        self,
        f1_text: str,
        f2_text: str,
        f3_data: Dict[str, Any],
        client: Optional[Any] = None,
        mock: bool = False,
    ) -> AdversarialAuditResult:
        c19_breaches = self._check_c19_firewall(f1_text, f2_text, f3_data)
        if c19_breaches:
            return AdversarialAuditResult(
                status="VETO_TRIGGERED",
                hard_veto=True,
                c19_breach_detected=True,
                adversarial_score_penalty=100,
                indictment_summary=f"Rule C-19 Career Firewall Breach: {', '.join(c19_breaches)}",
                remediation_recommendations=["Strip all INTERNAL_ADVISORY and career leverage markers from canonical outputs."],
            )

        injections = self._check_prompt_injection(f1_text, f2_text, f3_data)
        if injections:
            return AdversarialAuditResult(
                status="VETO_TRIGGERED",
                hard_veto=True,
                prompt_injection_detected=True,
                adversarial_score_penalty=100,
                indictment_summary=f"Prompt Injection / Evasion Attempt Detected: {', '.join(injections)}",
                remediation_recommendations=["Remove adversarial instruction-override markers from candidate artifacts."],
            )

        ungrounded = self._check_entity_grounding(f1_text, f3_data)
        epistemic_issues = self._check_epistemic_inflation(f2_text)
        penalty = min(25, (len(ungrounded) * 5) + (len(epistemic_issues) * 3))
        has_defects = bool(ungrounded or epistemic_issues)
        status = "ADVISORY_DEFECTS" if has_defects else "CLEARED"
        summary = (
            f"Adversarial probe found {len(ungrounded)} ungrounded entities and {len(epistemic_issues)} epistemic warnings."
            if has_defects
            else "Adversarial probe cleared: zero C-19 leaks, valid grounding, and calibrated certainty."
        )
        recs: List[str] = []
        if ungrounded:
            recs.append(f"Anchor or remove ungrounded entity IDs: {', '.join(ungrounded[:5])}")
        if epistemic_issues:
            recs.append("Soft dogmatic claims with calibrated confidence or explicit transcript citations.")

        return AdversarialAuditResult(
            status=status,
            hard_veto=False,
            c19_breach_detected=False,
            ungrounded_entities=ungrounded,
            epistemic_warnings=epistemic_issues,
            adversarial_score_penalty=penalty,
            indictment_summary=summary,
            remediation_recommendations=recs,
        )

    def _check_c19_firewall(self, f1_text: str, f2_text: str, f3_data: Dict[str, Any]) -> List[str]:
        combined_text = f"{f1_text}\n{f2_text}\n{str(f3_data)}"
        return [p.search(combined_text).group(0) for p in PROHIBITED_C19_PATTERNS if p.search(combined_text)]

    def _check_entity_grounding(self, f1_text: str, f3_data: Dict[str, Any]) -> List[str]:
        ev_idx = f3_data.get("evidence_index") if isinstance(f3_data.get("evidence_index"), dict) else {}
        ev_blob = "\n".join(f"{v.get('text', '')} {v.get('exact_text', '')}" for v in ev_idx.values() if isinstance(v, dict)).lower()
        f1_lower = f"{f1_text.lower()}\n{ev_blob}"
        f1_lower_norm = f1_lower.replace(" & ", " and ")
        entity_upserts = (f3_data.get("graph_mutations") or {}).get("entity_upserts", [])
        return [str(ent.get("id", "")) for ent in entity_upserts if isinstance(ent, dict) and not _is_single_entity_grounded(ent, f1_lower, f1_lower_norm)]

    def _check_epistemic_inflation(self, f2_text: str) -> List[str]:
        return [p.search(f2_text).group(0) for p in EPISTEMIC_INFLATION_PATTERNS if p.search(f2_text)]

    def _check_prompt_injection(self, f1_text: str, f2_text: str, f3_data: Dict[str, Any]) -> List[str]:
        combined_text = f"{f1_text}\n{f2_text}\n{str(f3_data)}"
        return [p.search(combined_text).group(0) for p in PROMPT_INJECTION_PATTERNS if p.search(combined_text)]


class CounterfactualRecallError(RuntimeError):
    """Raised when Gate C3 counterfactual recall drops below threshold."""


def _split_source_into_units(source_text: str) -> List[Tuple[str, str]]:
    if not source_text or not source_text.strip():
        return []
    pattern = re.compile(r"(?m)^(?:##\s*(?:Page|Slide|Sheet)\s*[^\n]+|###\s*Sheet:\s*[^\n]+|##\s+[^\n]+)")
    matches = list(pattern.finditer(source_text))
    if not matches:
        paras = [p.strip() for p in re.split(r"\n\s*\n", source_text) if len(p.strip()) >= 25]
        return [(f"unit:{idx + 1}", p) for idx, p in enumerate(paras)] or [("unit:1", source_text.strip())]

    units: List[Tuple[str, str]] = []
    for idx, m in enumerate(matches):
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(source_text)
        body = source_text[start:end].strip()
        if len(body) >= 20:
            units.append((m.group(0).strip("# ").strip(), body))
    return units or [("unit:1", source_text.strip())]


def _select_stratified_indices(total: int, max_units: int = 5) -> List[int]:
    if total <= 0:
        return []
    if total <= max_units:
        return list(range(total))
    return sorted({0, max(0, total // 4), max(0, total // 2), max(0, (3 * total) // 4), total - 1})


def _probe_from_line(locator: str, ln: str) -> Optional[Dict[str, str]]:
    if ln.startswith(("#", "---", "|---", "| ---")) or len(ln) < 12:
        return None
    if ln.startswith("|") and ln.endswith("|"):
        cells = [c.strip() for c in ln.strip("|").split("|") if c.strip()]
        low = [c.lower() for c in cells]
        skip_hdrs = {"area", "sub capabilities", "field id", "asset category", "workstream"}
        if not cells or all(c in ("nan", "none", "null", "unnamed") for c in low) or any(h in low for h in skip_hdrs):
            return None
        anchor = cells[1] if len(cells) >= 2 and len(cells[1]) >= 3 else cells[0]
        return {"locator": locator, "anchor": anchor, "fact_line": " | ".join(cells[:4])}
    cleaned = re.sub(r"^[-*•\d.)\s]+", "", ln).strip()
    if len(cleaned) < 18:
        return None
    return {"locator": locator, "anchor": " ".join(cleaned.split()[:5]), "fact_line": cleaned[:140]}


def _extract_unit_fact_probes(locator: str, unit_text: str, max_per_unit: int = 2) -> List[Dict[str, str]]:
    probes: List[Dict[str, str]] = []
    for ln in (line.strip() for line in unit_text.splitlines() if line.strip()):
        probe = _probe_from_line(locator, ln)
        if probe:
            probes.append(probe)
            if len(probes) >= max_per_unit:
                break
    return probes


def sample_stratified_source_probes(source_text: str, max_probes: int = 10) -> List[Dict[str, str]]:
    """Sample up to max_probes facts across stratified positions (1, N/4, N/2, 3N/4, N)."""
    units = _split_source_into_units(source_text)
    if not units:
        return []
    strat_indices = _select_stratified_indices(len(units), max_units=max(5, max_probes // 2))
    per_unit = max(1, max_probes // max(1, len(strat_indices)))
    probes: List[Dict[str, str]] = []
    for idx in strat_indices:
        loc, body = units[idx]
        probes.extend(_extract_unit_fact_probes(loc, body, max_per_unit=per_unit + 1))
    if len(probes) < max_probes:
        for idx, (loc, body) in enumerate(units):
            if idx not in strat_indices:
                probes.extend(_extract_unit_fact_probes(loc, body, max_per_unit=1))
                if len(probes) >= max_probes:
                    break
    return probes[:max_probes]


def _normalize_probe_token(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s&.%/-]", " ", text.lower())).strip()


def _build_projection_haystack(projection: Dict[str, Any]) -> str:
    ev_idx = projection.get("evidence_index") or {}
    ev_parts = [
        str(ev.get("exact_text") or ev.get("text") or "")
        for ev in (ev_idx.values() if isinstance(ev_idx, dict) else [])
        if isinstance(ev, dict)
    ]
    gm = projection.get("graph_mutations") or {}
    keys = (
        "entity_upserts", "relationship_signals", "decision_updates", "proposal_updates",
        "risk_updates", "action_updates", "strategic_intent_signals", "metrics",
    )
    gm_parts = [
        json.dumps(item, ensure_ascii=False)
        for k in (keys if isinstance(gm, dict) else ())
        for item in (gm.get(k) or [])
        if isinstance(item, dict)
    ]
    return _normalize_probe_token(" ".join(ev_parts + gm_parts))


def _query_canonical_assertions(conn: Any, asrt_cols: set[str], source_id: str) -> list:
    if "scope_text" in asrt_cols and "primary_source_id" in asrt_cols:
        sql = """SELECT COALESCE(a.scope_text, a.object_literal, '')
                 FROM assertion a
                 LEFT JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id
                 LEFT JOIN evidence_item e ON ae.evidence_id = e.evidence_id
                 WHERE (a.primary_source_id = ? OR e.source_id = ?)
                   AND a.canonical_status = 'canonical' AND a.canonical_eligible = 1"""
        return conn.execute(sql, (source_id, source_id)).fetchall()
    if "scope_text" in asrt_cols:
        sql = """SELECT COALESCE(a.scope_text, a.object_literal, '')
                 FROM assertion a
                 LEFT JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id
                 LEFT JOIN evidence_item e ON ae.evidence_id = e.evidence_id
                 WHERE e.source_id = ?
                   AND a.canonical_status = 'canonical' AND a.canonical_eligible = 1"""
        return conn.execute(sql, (source_id,)).fetchall()
    sql = """SELECT COALESCE(a.claim_text, '')
             FROM assertion a
             LEFT JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id
             LEFT JOIN evidence_item e ON ae.evidence_id = e.evidence_id
             WHERE e.source_id = ?
               AND a.canonical_status = 'canonical' AND a.canonical_eligible = 1"""
    return conn.execute(sql, (source_id,)).fetchall()


def _build_db_haystack(db_path: Path, source_id: str) -> str:
    conn = GovernedStorageContext.connect(Path(db_path).resolve(), readonly=True)
    try:
        asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
        ev_cols = {r[1] for r in conn.execute("PRAGMA table_info(evidence_item)").fetchall()}
        rows = _query_canonical_assertions(conn, asrt_cols, source_id)
        if "direct_quote" in ev_cols:
            ev_rows = conn.execute("SELECT COALESCE(direct_quote, '') FROM evidence_item WHERE source_id = ?", (source_id,)).fetchall()
        else:
            ev_rows = conn.execute("SELECT COALESCE(quote_text, '') FROM evidence_item WHERE source_id = ?", (source_id,)).fetchall()
        return _normalize_probe_token(" ".join([str(r[0] or "") for r in rows] + [str(r[0] or "") for r in ev_rows]))
    finally:
        conn.close()


def _probe_matches_haystack(probe: Dict[str, str], haystack: str) -> bool:
    anchor_norm = _normalize_probe_token(probe.get("anchor") or "")
    if not anchor_norm:
        return False
    if anchor_norm in haystack:
        return True
    tokens = [t for t in anchor_norm.split() if len(t) >= 2 or any(c.isdigit() for c in t)]
    if not tokens or any(any(c.isdigit() for c in t) and t not in haystack for t in tokens):
        return False
    return (sum(1 for t in tokens if t in haystack) / len(tokens)) >= 0.90


def run_counterfactual_recall_probe(
    source_text: str,
    *,
    projection: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
    source_id: Optional[str] = None,
    min_recall: float = 0.90,
    max_probes: int = 10,
    enforce: bool = False,
) -> Dict[str, Any]:
    """Execute Gate C3 stratified counterfactual recall probe."""
    probes = sample_stratified_source_probes(source_text, max_probes=max_probes)
    if not probes:
        return {
            "gate": "Gate_C3_Counterfactual_Recall_Probe",
            "status": "PASS",
            "total_probes": 0,
            "matched_probes": 0,
            "recall": 1.0,
            "min_recall": min_recall,
            "missed_probes": [],
            "defect_codes": [],
        }

    haystack = ""
    if isinstance(projection, dict):
        haystack += " " + _build_projection_haystack(projection)
    if db_path is not None and source_id:
        haystack += " " + _build_db_haystack(Path(db_path), source_id)

    matched = [p for p in probes if _probe_matches_haystack(p, haystack)]
    missed = [p for p in probes if not _probe_matches_haystack(p, haystack)]
    recall = round(len(matched) / len(probes), 4)
    passed = recall >= min_recall
    defect_codes = [] if passed else ["ERR_COUNTERFACTUAL_RECALL_DEFICIT", "ERR_OMITTED_WORKSTREAM_NODES"]

    receipt = {
        "gate": "Gate_C3_Counterfactual_Recall_Probe",
        "status": "PASS" if passed else "FAIL",
        "total_probes": len(probes),
        "matched_probes": len(matched),
        "recall": recall,
        "min_recall": min_recall,
        "missed_probes": missed,
        "defect_codes": defect_codes,
    }
    if enforce and not passed:
        raise CounterfactualRecallError(
            f"Gate C3 failed: counterfactual recall {recall:.1%} < {min_recall:.1%} "
            f"(missed {len(missed)}/{len(probes)} stratified probes)"
        )
    return receipt
