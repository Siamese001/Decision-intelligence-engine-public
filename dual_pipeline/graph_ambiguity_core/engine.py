"""Unified Knowledge Graph Ambiguity Engine (Domains G1-G4)."""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

from .detector_g1_entity import G1EntityIdentityDetector
from .detector_g2_predicates import G2PredicateDirectionalityDetector
from .detector_g3_epistemic import G3EpistemicClaimDetector
from .detector_g4_bipartite import G4BipartiteC19Detector
from .models import GraphAmbiguityDomain, GraphAmbiguityRecord

logger = logging.getLogger(__name__)


class GraphAmbiguityEngine:
    """Unified ambiguity scanning and pre-synthesis gating engine for Output 3 (Knowledge Graph)."""

    def __init__(
        self,
        margin_threshold: Optional[float] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        from dual_pipeline.model_registry import ModelTier, resolve_tier_model
        from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

        resolved_model, resolved_effort = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        self.margin_threshold = (
            margin_threshold
            if margin_threshold is not None
            else get_hitl_ambiguity_margin_threshold()
        )
        self.model = model or resolved_model
        self.reasoning_effort = reasoning_effort or resolved_effort or "high"
        self.d_g1 = G1EntityIdentityDetector()
        self.d_g2 = G2PredicateDirectionalityDetector()
        self.d_g3 = G3EpistemicClaimDetector()
        self.d_g4 = G4BipartiteC19Detector()

    def scan_all(
        self,
        f1_text: str = "",
        f3_candidate: Optional[Dict[str, Any]] = None,
    ) -> List[GraphAmbiguityRecord]:
        """Scan meeting context and graph candidate across all 4 knowledge graph ambiguity domains."""
        all_records: List[GraphAmbiguityRecord] = []

        # Domain G1: Entity Identity Resolution & Namespace
        g1_recs = self.d_g1.detect(f1_text, f3_candidate)
        all_records.extend(g1_recs)

        # Domain G2: Predicate Specificity & Directionality
        g2_recs = self.d_g2.detect(f1_text, f3_candidate)
        all_records.extend(g2_recs)

        # Domain G3: Epistemic Status Calibration
        g3_recs = self.d_g3.detect(f1_text, f3_candidate)
        all_records.extend(g3_recs)

        # Domain G4: Bipartite Partition & Rule C-19 Firewall
        g4_recs = self.d_g4.detect(f1_text, f3_candidate)
        all_records.extend(g4_recs)

        return all_records

    def scan(
        self,
        f1_text: str = "",
        f3_candidate: Optional[Dict[str, Any]] = None,
    ) -> List[GraphAmbiguityRecord]:
        """Scan and return only gate-eligible ambiguities (margin delta < margin_threshold)."""
        all_records = self.scan_all(f1_text, f3_candidate)
        gate_eligible = [r for r in all_records if r.is_gate_eligible(self.margin_threshold)]
        logger.info(
            f"GraphAmbiguityEngine: scanned context, found {len(all_records)} total ambiguities, "
            f"{len(gate_eligible)} gate-eligible (margin < {self.margin_threshold:.2f})."
        )
        return gate_eligible

    def apply_resolutions(
        self,
        f3_candidate: Dict[str, Any],
        resolved_records: List[GraphAmbiguityRecord],
    ) -> Dict[str, Any]:
        """Apply resolved operator choices to update the candidate Output 03 payload."""
        updated = copy.deepcopy(f3_candidate)
        mutations = updated.setdefault("graph_mutations", {})

        for rec in resolved_records:
            if rec.status != "RESOLVED" or not rec.selected_key or not rec.selected_option:
                continue
            meta = rec.selected_option.metadata

            # Handle G1 prefix replacement
            if rec.domain == GraphAmbiguityDomain.G1_ENTITY_IDENTITY_NAMESPACE:
                orig_id = meta.get("original_id")
                new_id = rec.selected_key
                for ent in mutations.get("entity_upserts", []):
                    if ent.get("entity_id") == orig_id:
                        ent["entity_id"] = new_id

            # Handle G2 predicate replacement
            elif rec.domain == GraphAmbiguityDomain.G2_PREDICATE_DIRECTIONALITY:
                from_id = meta.get("from_id")
                to_id = meta.get("to_id")
                new_pred = meta.get("predicate")
                for rel in mutations.get("relationship_signals", []):
                    if rel.get("from_id") == from_id and rel.get("to_id") == to_id:
                        rel["relationship"] = new_pred

            # Handle G3 epistemic promotion/demotion
            elif rec.domain == GraphAmbiguityDomain.G3_EPISTEMIC_CLAIM_STATUS:
                target_array = meta.get("target_array")
                if target_array == "proposal_updates" and "decision_id" in meta:
                    dec_id = meta["decision_id"]
                    dec_list = mutations.get("decision_updates", [])
                    moved = [d for d in dec_list if d.get("decision_id") == dec_id]
                    mutations["decision_updates"] = [
                        d for d in dec_list if d.get("decision_id") != dec_id
                    ]
                    for d in moved:
                        d["proposal_id"] = d.pop(
                            "decision_id", f"proposal:{len(mutations.get('proposal_updates', []))}"
                        )
                        mutations.setdefault("proposal_updates", []).append(d)
                elif target_array == "decision_updates" and "proposal_id" in meta:
                    prop_id = meta["proposal_id"]
                    prop_list = mutations.get("proposal_updates", [])
                    moved = [p for p in prop_list if p.get("proposal_id") == prop_id]
                    mutations["proposal_updates"] = [
                        p for p in prop_list if p.get("proposal_id") != prop_id
                    ]
                    for p in moved:
                        p["decision_id"] = p.pop(
                            "proposal_id", f"decision:{len(mutations.get('decision_updates', []))}"
                        )
                        mutations.setdefault("decision_updates", []).append(p)

        return updated
