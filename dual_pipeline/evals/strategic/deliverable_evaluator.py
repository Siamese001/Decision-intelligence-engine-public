"""Live Pipeline Deliverable Strategic Evaluator.

Grades authentic meeting deliverables (Outputs 01, 02, 03) directly against
the 10 Strategic Dimensions of the $1.2M Executive Cognitive Exoskeleton:
1. Enterprise Architecture & Business Capability Depth
2. Multi-Hop Connection Mining & Latent Linkage
3. Cross-Silo Contradiction & Conflict Discovery
4. Dual-Audience Federation & Career Leverage Firewall (Rule C-19)
5. Conway's Law, Politics & Organizational Topology
6. Ingestion Signal vs. Noise & Epistemic Alpha
7. Bi-Temporal Rigor & Trust Foundation
8. Dialectical Decision Intel & Multi-Criteria Trade-offs
9. Board-Level Synthesis & Next Best Action (NBA)
10. Multi-Client Federation & Scalable Knowledge Asset
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from dual_pipeline.evals.strategic.models import (
    DimensionScore,
    ExecutiveExoskeletonScorecard,
)


class DeliverableStrategicEvaluator:
    """Evaluates authentic pipeline deliverables (01, 02, 03) against strategic dimensions."""

    CAREER_FIREWALL_TERMS = [
        r"\$1\.2m\b",
        r"1\.2m trajectory",
        r"compensation leverage",
        r"career trajectory",
        r"personal advancement",
        r"stealth_score",
        r"political power map",
        r"executive career notes",
    ]

    RULE9_ANTI_AI_PATTERNS = [
        r"\u2014",
        r"\u2013",
        r"\s--\s",
        r"\b(?:delve|testament|tapestry|crucial|pivotal|foster|underscore|moreover|furthermore|synergy|holistic|paradigm|seamless)\b",
    ]

    def __init__(self) -> None:
        self._regex_firewall = [
            re.compile(pattern, re.IGNORECASE) for pattern in self.CAREER_FIREWALL_TERMS
        ]
        self._regex_rule9 = [
            re.compile(pattern, re.IGNORECASE) for pattern in self.RULE9_ANTI_AI_PATTERNS
        ]

    def evaluate_deliverables(
        self,
        output_01_path: Union[str, Path],
        output_02_path: Union[str, Path],
        output_03_path: Union[str, Path],
        advisory_path: Optional[Union[str, Path]] = None,
    ) -> ExecutiveExoskeletonScorecard:
        """Parse authentic artifacts and score the 10 Strategic Intelligence Dimensions."""
        p1 = Path(output_01_path)
        p2 = Path(output_02_path)
        p3 = Path(output_03_path)

        text_01 = p1.read_text(encoding="utf-8") if p1.exists() else ""
        text_02 = p2.read_text(encoding="utf-8") if p2.exists() else ""
        data_03 = json.loads(p3.read_text(encoding="utf-8")) if p3.exists() else {}

        advisory_data: Dict[str, Any] = {}
        if advisory_path and Path(advisory_path).exists():
            try:
                advisory_data = json.loads(Path(advisory_path).read_text(encoding="utf-8"))
            except Exception:
                advisory_data = {"raw": Path(advisory_path).read_text(encoding="utf-8")}

        scorecard = ExecutiveExoskeletonScorecard()
        scorecard.dimension_scores["dim_1_ea_capability"] = self._score_dim_1_ea(data_03)
        scorecard.dimension_scores["dim_2_latent_multihop"] = self._score_dim_2_multihop(data_03)
        scorecard.dimension_scores["dim_3_contradictions"] = self._score_dim_3_contradictions(data_03, text_02)
        scorecard.dimension_scores["dim_4_dual_audience"] = self._score_dim_4_firewall(text_01, text_02, advisory_data, data_03)
        scorecard.dimension_scores["dim_5_conways_law"] = self._score_dim_5_conway(data_03, text_02)
        scorecard.dimension_scores["dim_6_epistemic_alpha"] = self._score_dim_6_epistemic(data_03)
        scorecard.dimension_scores["dim_7_bitemporal"] = self._score_dim_7_temporal(data_03)
        scorecard.dimension_scores["dim_8_mcda_tradeoffs"] = self._score_dim_8_tradeoffs(data_03, text_02)
        scorecard.dimension_scores["dim_9_board_synthesis"] = self._score_dim_9_board_nba(text_02)
        scorecard.dimension_scores["dim_10_multiclient_federation"] = self._score_dim_10_federation(data_03)

        scorecard.compute_summary(passing_threshold=75.0)
        return scorecard

    def _score_dim_1_ea(self, data_03: Dict[str, Any]) -> DimensionScore:
        """Dim 1: Hierarchical Business Capabilities & structured decisions in 03."""
        mutations = data_03.get("graph_mutations", {})
        entities = mutations.get("entity_upserts", [])
        decisions = mutations.get("decision_updates", [])
        strat_text = data_03.get("_strategic_analysis", "")

        has_systems = any(
            str(e.get("id", e.get("entity_id", ""))).startswith("system:") or e.get("type") == "system"
            for e in entities
        )
        has_workstreams = any(
            str(e.get("id", e.get("entity_id", ""))).startswith(("workstream:", "project:"))
            or e.get("type") in ("workstream", "project")
            for e in entities
        )
        has_capabilities = bool(strat_text or has_systems or has_workstreams)
        has_adr = len(decisions) > 0 or ("consolidation" in strat_text.lower())

        passed = has_capabilities and has_adr
        score = 100.0 if passed else (60.0 if has_capabilities else 20.0)
        return DimensionScore(
            dim_id="dim_1_ea_capability",
            name="Enterprise Architecture & Capability Depth",
            score=score,
            passed=passed,
            details={"entities_count": len(entities), "decisions_count": len(decisions), "has_adr": has_adr},
        )


    def _score_dim_2_multihop(self, data_03: Dict[str, Any]) -> DimensionScore:
        """Dim 2: Multi-hop graph connectivity discovery."""
        signals = data_03.get("graph_mutations", {}).get("relationship_signals", [])
        adj: Dict[str, List[str]] = {}
        for s in signals:
            src = s.get("from_id")
            dst = s.get("to_id")
            if src and dst:
                adj.setdefault(src, []).append(dst)

        def max_depth(curr: str, visited: Set[str]) -> int:
            d = 0
            for neighbor in adj.get(curr, []):
                if neighbor not in visited:
                    d = max(d, 1 + max_depth(neighbor, visited | {neighbor}))
            return d

        deepest = max((max_depth(n, {n}) for n in adj), default=0)
        passed = deepest >= 3 or len(signals) >= 3
        score = 100.0 if deepest >= 4 else (90.0 if (deepest >= 3 or len(signals) >= 3) else 50.0)
        return DimensionScore(
            dim_id="dim_2_latent_multihop",
            name="Multi-Hop Latent Connection Mining",
            score=score,
            passed=passed,
            details={"max_discovered_hops": deepest, "total_relationship_signals": len(signals)},
        )

    def _score_dim_3_contradictions(self, data_03: Dict[str, Any], text_02: str) -> DimensionScore:
        """Dim 3: Cross-silo contradictions, friction, or conflicting constraints."""
        risks = data_03.get("graph_mutations", {}).get("risk_updates", [])
        proposals = data_03.get("graph_mutations", {}).get("proposal_updates", [])
        has_risks = len(risks) > 0
        has_conflicts_text = bool(
            re.search(r"\b(conflict|tradeoff|versus|headwind|friction|divergence)\b", text_02, re.I)
        )
        passed = has_risks or has_conflicts_text or (len(proposals) > 0)
        score = 100.0 if (has_risks and has_conflicts_text) else (85.0 if passed else 40.0)
        return DimensionScore(
            dim_id="dim_3_contradictions",
            name="Cross-Silo Contradiction & Conflict Discovery",
            score=score,
            passed=passed,
            details={"risks_recorded": len(risks), "friction_in_recap": has_conflicts_text},
        )

    def _score_dim_4_firewall(
        self,
        text_01: str,
        text_02: str,
        advisory_data: Dict[str, Any],
        data_03: Optional[Dict[str, Any]] = None,
    ) -> DimensionScore:
        """Dim 4: Strict C-19 Career Leverage Firewall isolation across Outputs 01, 02, and 03."""
        text_03 = json.dumps(data_03, sort_keys=True) if data_03 else ""
        leaks_01 = [p.pattern for p in self._regex_firewall if p.search(text_01)]
        leaks_02 = [p.pattern for p in self._regex_firewall if p.search(text_02)]
        leaks_03 = [p.pattern for p in self._regex_firewall if p.search(text_03)]
        total_leaks = len(leaks_01) + len(leaks_02) + len(leaks_03)

        passed = total_leaks == 0
        score = 100.0 if passed else 0.0
        return DimensionScore(
            dim_id="dim_4_dual_audience",
            name="Dual-Audience Federation & Career Leverage Firewall",
            score=score,
            passed=passed,
            details={
                "output_01_leaks": leaks_01,
                "output_02_leaks": leaks_02,
                "output_03_leaks": leaks_03,
                "total_leaks": total_leaks,
            },
        )

    def _score_dim_5_conway(self, data_03: Dict[str, Any], text_02: str) -> DimensionScore:
        """Dim 5: Conway's Law, Politics & Organizational Topology."""
        entities = data_03.get("graph_mutations", {}).get("entity_upserts", [])
        persons = [
            e for e in entities
            if str(e.get("id", e.get("entity_id", ""))).startswith("person:") or e.get("type") == "person"
        ]
        orgs = [
            e for e in entities
            if str(e.get("id", e.get("entity_id", ""))).startswith("org:") or e.get("type") == "org"
        ]
        has_stakeholders = len(persons) >= 1 or len(orgs) >= 1 or ("Attendees" in text_02)
        has_endorsement = bool(re.search(r"\b(endorsement|finance|governance|sponsor|owner)\b", text_02, re.I))

        passed = has_stakeholders and has_endorsement
        score = 100.0 if passed else 50.0
        return DimensionScore(
            dim_id="dim_5_conways_law",
            name="Conway's Law, Politics & Organizational Topology",
            score=score,
            passed=passed,
            details={"persons": len(persons), "orgs": len(orgs), "governance_endorsement": has_endorsement},
        )


    def _score_dim_6_epistemic(self, data_03: Dict[str, Any]) -> DimensionScore:
        """Dim 6: Ingestion Signal vs. Noise & Epistemic Alpha."""
        evidence_idx = data_03.get("evidence_index", {})
        has_grounding = len(evidence_idx) >= 2
        kinds = {item.get("kind") for item in evidence_idx.values()}
        has_multi_kind = len(kinds) >= 2

        passed = has_grounding and has_multi_kind
        score = 100.0 if passed else (75.0 if has_grounding else 40.0)
        return DimensionScore(
            dim_id="dim_6_epistemic_alpha",
            name="Ingestion Signal vs. Noise & Epistemic Alpha",
            score=score,
            passed=passed,
            details={"evidence_items_count": len(evidence_idx), "kinds_observed": list(kinds)},
        )

    def _score_dim_7_temporal(self, data_03: Dict[str, Any]) -> DimensionScore:
        """Dim 7: Bi-Temporal Rigor & Trust Foundation."""
        evidence_idx = data_03.get("evidence_index", {})
        has_locators = all(
            bool(item.get("locator", {}).get("value")) for item in evidence_idx.values()
        ) if evidence_idx else False
        meeting_id = str(data_03.get("meeting_id", ""))
        observed_at = str(data_03.get("observed_at", "") or data_03.get("date", ""))
        temporal_blob = f"{meeting_id} {observed_at}"
        has_date_tag = bool(
            re.search(r"\d{8}|\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4}", temporal_blob)
        )

        passed = has_locators and has_date_tag
        score = 100.0 if passed else 60.0
        return DimensionScore(
            dim_id="dim_7_bitemporal",
            name="Bi-Temporal Rigor & Trust Foundation",
            score=score,
            passed=passed,
            details={"has_locators": has_locators, "meeting_id": meeting_id, "observed_at": observed_at},
        )

    def _score_dim_8_tradeoffs(self, data_03: Dict[str, Any], text_02: str) -> DimensionScore:
        """Dim 8: Dialectical Decision Intel & Multi-Criteria Trade-offs."""
        has_quant_model = bool(
            re.search(r"(\$\d+|\bhours saved\b|\bvaluation\b|\blabor\b|\bcost avoidance\b)", text_02, re.I)
        )
        has_prioritization = bool(
            re.search(r"\b(tradeoff|prioritization|compromise|scope|soft vs\. hard)\b", text_02, re.I)
        )
        passed = has_quant_model and has_prioritization
        score = 100.0 if passed else (75.0 if (has_quant_model or has_prioritization) else 50.0)
        return DimensionScore(
            dim_id="dim_8_mcda_tradeoffs",
            name="Dialectical Decision Intel & Multi-Criteria Trade-offs",
            score=score,
            passed=passed,
            details={"quant_model": has_quant_model, "prioritization": has_prioritization},
        )

    def _score_dim_9_board_nba(self, text_02: str) -> DimensionScore:
        """Dim 9: Board-Level Synthesis, Rule 9 Anti-AI Hygiene & SBAI+ Backing."""
        has_objective = "Meeting Objective" in text_02
        has_financials = bool(re.search(r"\$\d{1,3}(,\d{3})*(\.\d+)?", text_02))
        has_next_steps = bool(re.search(r"\b(next steps|actionable next steps|cadence|deadlines)\b", text_02, re.I))
        rule9_violations = [p.pattern for p in self._regex_rule9 if p.search(text_02)]
        rule9_clean = len(rule9_violations) == 0

        is_perf_review = bool(re.search(r"\b(SBAI\+|performance review|competency rubric)\b", text_02, re.I))
        sbai_backed = (
            bool(re.search(r"\b(Situation|Behavior|Action|Impact|Not Observed in Graph/Transcripts)\b", text_02, re.I))
            if is_perf_review
            else True
        )

        passed = has_objective and has_financials and has_next_steps and rule9_clean and sbai_backed
        score = 100.0 if passed else (0.0 if not (rule9_clean and sbai_backed) else 50.0)
        return DimensionScore(
            dim_id="dim_9_board_synthesis",
            name="Board-Level Synthesis & Next Best Action (NBA)",
            score=score,
            passed=passed,
            details={
                "has_objective": has_objective,
                "has_financials": has_financials,
                "has_next_steps": has_next_steps,
                "rule9_anti_ai_clean": rule9_clean,
                "rule9_violations": rule9_violations,
                "sbai_competency_backed": sbai_backed,
            },
        )

    def _score_dim_10_federation(self, data_03: Dict[str, Any]) -> DimensionScore:
        """Dim 10: Multi-Client Federation & Scalable Knowledge Asset."""
        strat = data_03.get("_strategic_analysis", "")
        has_general_pattern = bool(
            re.search(r"\b(consolidation|efficiency|automation|allocation|admission)\b", strat, re.I)
        )
        passed = has_general_pattern and bool(data_03.get("schema_version"))
        score = 100.0 if passed else 60.0
        return DimensionScore(
            dim_id="dim_10_multiclient_federation",
            name="Multi-Client Federation & Scalable Knowledge Asset",
            score=score,
            passed=passed,
            details={"general_pattern": has_general_pattern, "schema_version": data_03.get("schema_version")},
        )
