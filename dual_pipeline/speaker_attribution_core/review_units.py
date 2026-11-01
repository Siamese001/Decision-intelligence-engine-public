"""Grouped human review units, conflicting-anchor guards, and legacy crosswalk.

Governs Wave 4 human-in-the-loop review abstraction:
- Groups homogeneous speaker clusters and bounded ambiguous passages into coherent review items.
- Protects manually confirmed voice anchors from silent overwrite via ConflictingAnchorError.
- Supports operator actions: confirm_all, confirm_with_exceptions, reject_split, cannot_tell.
- Constructs deterministic legacy crosswalk mapping every historical fragment to canonical utterances.
- Migrates review workload denominator from raw fragment count (426) to proposed review units.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from .legacy_crosswalk import LegacyCrosswalkEntry, build_legacy_crosswalk
from .review_context import SpeakerReviewContextError
from .review_context_units import build_grouped_review_contexts, grouped_context_digest, render_grouped_contexts

logger = logging.getLogger(__name__)

VALID_DECISION_SCOPES: frozenset[str] = frozenset({
    "attendance",
    "role",
    "employer",
    "cluster_identity",
    "dominant_speaker_share",
    "individual_utterance",
})


class ConflictingAnchorError(ValueError):
    """Raised when a grouped review decision conflicts with an established verified anchor."""
    pass


@dataclass
class ReviewItem:
    """Grouped review card conforming to speaker_utterance.schema.json reviewItem."""
    review_id: str
    item_type: str  # "homogeneous_cluster", "mixed_cluster_split", "uncertain_passage", "boundary_overlap"
    target_utterance_ids: List[str]
    context_start_sec: float
    context_end_sec: float
    candidate_speakers: List[str]
    resolution_status: str = "pending"  # "pending", "resolved", "cannot_tell"
    audio_channel: Optional[Union[int, str]] = None
    assigned_speaker: Optional[str] = None
    word_ids: List[str] = field(default_factory=list)
    locked_anchor_ids: List[str] = field(default_factory=list)
    linked_cluster_ids: List[str] = field(default_factory=list)
    review_contexts: List[Dict[str, Any]] = field(default_factory=list)
    context_status: str = "unavailable"

    @property
    def context_digest(self) -> str:
        return grouped_context_digest(self.review_contexts) if self.review_contexts else ""

    def visible_context(self) -> str:
        """Return the exact evidence block suitable for Antigravity's visible context field."""
        if self.context_status != "ready":
            raise SpeakerReviewContextError("Speaker assignment is blocked because visible review context is unavailable.")
        return render_grouped_contexts(self.review_contexts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "review_id": self.review_id,
            "item_type": self.item_type,
            "target_utterance_ids": self.target_utterance_ids,
            "context_start_sec": round(self.context_start_sec, 4),
            "context_end_sec": round(self.context_end_sec, 4),
            "audio_channel": self.audio_channel,
            "candidate_speakers": self.candidate_speakers,
            "assigned_speaker": self.assigned_speaker,
            "resolution_status": self.resolution_status,
            "linked_cluster_ids": self.linked_cluster_ids,
            "context_status": self.context_status,
            "review_contexts": self.review_contexts,
            "context_digest": self.context_digest,
        }


@dataclass
class ReviewDecision:
    """Explicit human operator resolution payload."""
    decision_id: str
    review_id: str
    action: str  # "confirm_all", "confirm_with_exceptions", "reject_split", "cannot_tell", "undo"
    assigned_speaker: Optional[str]
    scope_utterance_ids: List[str]
    scope_word_ids: List[str]
    decision_scope: str = "cluster_identity"
    operator_origin: str = "human_operator"  # "human_operator" vs "autonomous_bypass"
    signature: Optional[str] = None
    recorded_at: Optional[str] = None
    context_digest: Optional[str] = None
    exceptions: Dict[str, str] = field(default_factory=dict)
    notes: str = ""
    previous_state: Optional[Dict[str, str]] = None

    def __post_init__(self) -> None:
        if self.decision_scope not in VALID_DECISION_SCOPES:
            raise ValueError(
                f"Invalid decision scope '{self.decision_scope}'. Must be one of {sorted(VALID_DECISION_SCOPES)}"
            )
        if self.operator_origin == "autonomous_bypass" and not self.signature:
            raise ValueError("Autonomous bypass decisions without valid signature are barred.")


class ReviewUnitManager:
    """Manages creation, guard checking, and execution of grouped review items."""

    def __init__(self, confirmed_attendees: Optional[Set[str]] = None):
        self.confirmed_attendees = confirmed_attendees or {
            "Amit Ayer", "Jake Stark", "Morgan Vance Krutilla", "Jennifer Pan"
        }

    def generate_review_items(
        self,
        utterances: List[Dict[str, Any]],
        verified_anchors: Optional[Dict[str, str]] = None,
    ) -> List[ReviewItem]:
        """
        Groups utterances into proposed review items:
        1. Homogeneous acoustic clusters (e.g. SPEAKER_00 turns).
        2. Uncertain or ambiguous passages (e.g. Unknown or overlap).
        """
        anchors = verified_anchors or {}
        items: List[ReviewItem] = []

        # Group by raw speaker / cluster
        cluster_map: Dict[str, List[Dict[str, Any]]] = {}
        uncertain_utts: List[Dict[str, Any]] = []

        for u in utterances:
            spk = u.get("speaker", "Unknown")
            is_overlap = u.get("overlap_context", False)

            if spk == "Unknown" or is_overlap:
                uncertain_utts.append(u)
            else:
                cluster_map.setdefault(spk, []).append(u)

        item_idx = 1

        # 1. Homogeneous clusters
        for cl_spk, cl_utts in sorted(cluster_map.items(), key=lambda x: x[0]):
            target_ids = [x["utterance_id"] for x in cl_utts]
            word_ids = [w for x in cl_utts for w in x.get("word_ids", [])]
            st = min(float(x.get("start_time_sec", x.get("start", 0.0))) for x in cl_utts)
            en = max(float(x.get("end_time_sec", x.get("end", st))) for x in cl_utts)
            locked = [uid for uid in target_ids if uid in anchors]
            contexts, context_status = build_grouped_review_contexts(utterances, target_ids)

            # Candidate speakers: confirmed attendees
            candidates = sorted(list(self.confirmed_attendees))

            items.append(
                ReviewItem(
                    review_id=f"rev_cluster_{item_idx:03d}",
                    item_type="homogeneous_cluster",
                    target_utterance_ids=target_ids,
                    context_start_sec=st,
                    context_end_sec=en,
                    candidate_speakers=candidates,
                    resolution_status="resolved" if (cl_spk in self.confirmed_attendees and not locked) else "pending",
                    assigned_speaker=cl_spk if cl_spk in self.confirmed_attendees else None,
                    word_ids=word_ids,
                    locked_anchor_ids=locked,
                    review_contexts=contexts,
                    context_status=context_status,
                )
            )
            item_idx += 1

        # Keep uncertain turns atomic so each speaker choice has its own context window.
        for uncertain in uncertain_utts:
            target_id = uncertain["utterance_id"]
            st = float(uncertain.get("start_time_sec", uncertain.get("start", 0.0)))
            en = float(uncertain.get("end_time_sec", uncertain.get("end", st)))
            contexts, context_status = build_grouped_review_contexts(utterances, [target_id])
            items.append(
                ReviewItem(
                    review_id=f"rev_passage_{item_idx:03d}",
                    item_type="uncertain_passage",
                    target_utterance_ids=[target_id],
                    context_start_sec=st,
                    context_end_sec=en,
                    candidate_speakers=sorted(list(self.confirmed_attendees)) + ["Unknown"],
                    resolution_status="pending",
                    assigned_speaker=None,
                    word_ids=list(uncertain.get("word_ids", [])),
                    locked_anchor_ids=[target_id] if target_id in anchors else [],
                    review_contexts=contexts,
                    context_status=context_status,
                )
            )
            item_idx += 1

        return items

    @staticmethod
    def _check_anchor_conflicts(
        decision: ReviewDecision,
        anchors: Dict[str, str],
        allow_anchor_override: bool,
    ) -> None:
        if allow_anchor_override:
            return
        conflicts = []
        for uid in decision.scope_utterance_ids:
            if uid in anchors:
                exp = anchors[uid]
                intended = decision.exceptions.get(uid, decision.assigned_speaker)
                if intended and intended != exp:
                    conflicts.append((uid, exp, intended))
        if conflicts:
            details = ", ".join(f"{u}: verified as '{exp}' vs decision '{dec}'" for u, exp, dec in conflicts)
            raise ConflictingAnchorError(
                f"Conflicting anchor guard triggered: Decision {decision.decision_id} attempted to overwrite "
                f"manually verified anchor(s) without explicit override: {details}"
            )

    @staticmethod
    def _resolve_target_speaker(
        uid: str,
        decision: ReviewDecision,
        anchors: Dict[str, str],
        allow_anchor_override: bool,
    ) -> str:
        if uid in decision.exceptions:
            return decision.exceptions[uid]
        if uid in anchors and not allow_anchor_override:
            return anchors[uid]
        return decision.assigned_speaker or "Unknown"

    @staticmethod
    def _apply_confirm_action(
        decision: ReviewDecision,
        item: ReviewItem,
        utt_map: Dict[str, Dict[str, Any]],
        anchors: Dict[str, str],
        allow_anchor_override: bool,
    ) -> int:
        item.resolution_status = "resolved"
        item.assigned_speaker = decision.assigned_speaker
        count = 0
        for uid in decision.scope_utterance_ids:
            if uid in utt_map:
                utt_map[uid]["speaker"] = ReviewUnitManager._resolve_target_speaker(
                    uid, decision, anchors, allow_anchor_override
                )
                count += 1
        return count

    @staticmethod
    def _apply_cannot_tell_action(
        decision: ReviewDecision,
        item: ReviewItem,
        utt_map: Dict[str, Dict[str, Any]],
    ) -> int:
        item.resolution_status = "cannot_tell"
        item.assigned_speaker = None
        return 0

    @staticmethod
    def _apply_undo_action(
        decision: ReviewDecision,
        item: ReviewItem,
        utt_map: Dict[str, Dict[str, Any]],
    ) -> int:
        item.resolution_status = "pending"
        item.assigned_speaker = None
        count = 0
        for uid in decision.scope_utterance_ids:
            if uid in utt_map:
                prev = decision.previous_state.get(uid) if decision.previous_state else "Unknown"
                utt_map[uid]["speaker"] = prev
                count += 1
        return count

    @staticmethod
    def _mutate_utterance_speakers(
        decision: ReviewDecision,
        item: ReviewItem,
        utt_map: Dict[str, Dict[str, Any]],
        anchors: Dict[str, str],
        allow_anchor_override: bool,
    ) -> int:
        if decision.action == "undo":
            return ReviewUnitManager._apply_undo_action(decision, item, utt_map)
        if decision.action == "cannot_tell":
            return ReviewUnitManager._apply_cannot_tell_action(decision, item, utt_map)
        if decision.action == "reject_split":
            item.resolution_status = "pending"
            item.assigned_speaker = None
            return 0
        if decision.action in ("confirm_all", "confirm_with_exceptions"):
            return ReviewUnitManager._apply_confirm_action(
                decision, item, utt_map, anchors, allow_anchor_override
            )
        return 0

    @staticmethod
    def link_clusters(
        primary_item: ReviewItem,
        secondary_item: ReviewItem,
        rationale: str,
    ) -> None:
        """Links multiple acoustic clusters belonging to the same individual with explicit rationale."""
        if not rationale or not rationale.strip():
            raise ValueError("Linking multiple clusters to the same person requires explicit supporting rationale.")
        if secondary_item.review_id not in primary_item.linked_cluster_ids:
            primary_item.linked_cluster_ids.append(secondary_item.review_id)
        if primary_item.review_id not in secondary_item.linked_cluster_ids:
            secondary_item.linked_cluster_ids.append(primary_item.review_id)

    def apply_decision(
        self,
        decision: ReviewDecision,
        review_items: List[ReviewItem],
        utterances: List[Dict[str, Any]],
        verified_anchors: Optional[Dict[str, str]] = None,
        allow_anchor_override: bool = False,
    ) -> Dict[str, Any]:
        """
        Applies operator decision with ConflictingAnchorError guard.
        Guarantees that a cluster-level decision cannot silently overwrite a conflicting manually verified anchor.
        """
        anchors = verified_anchors or {}
        item = next((it for it in review_items if it.review_id == decision.review_id), None)
        if not item:
            raise KeyError(f"Review item not found: {decision.review_id}")

        if item.context_status != "ready" or decision.context_digest != item.context_digest:
            raise SpeakerReviewContextError("Speaker decision is blocked because its approval is not bound to the visible context.")
        self._check_anchor_conflicts(decision, anchors, allow_anchor_override)
        current_turns = {str(turn.get("utterance_id")): turn for turn in utterances}
        for context in item.review_contexts:
            for shown_turn in context.get("turns", []):
                current = current_turns.get(str(shown_turn.get("turn_id")))
                if current is None or current.get("text") != shown_turn.get("text") or str(current.get("speaker", "Unknown")) != shown_turn.get("speaker"):
                    raise SpeakerReviewContextError("Speaker decision is blocked because its visible transcript context is stale.")
        if sorted(decision.scope_utterance_ids) != sorted(item.target_utterance_ids):
            raise SpeakerReviewContextError("Speaker decisions must cover exactly the context-bound target turns.")

        utt_map = {u["utterance_id"]: u for u in utterances}
        modified_count = self._mutate_utterance_speakers(
            decision, item, utt_map, anchors, allow_anchor_override
        )
        item.review_contexts, item.context_status = build_grouped_review_contexts(
            utterances, item.target_utterance_ids
        )

        return {
            "decision_id": decision.decision_id,
            "review_id": decision.review_id,
            "action": decision.action,
            "modified_utterances": modified_count,
            "resolution_status": item.resolution_status,
        }

    def build_legacy_crosswalk(
        self,
        legacy_turns: List[Dict[str, Any]],
        canonical_utterances: List[Dict[str, Any]],
    ) -> List[LegacyCrosswalkEntry]:
        """
        Builds a versioned crosswalk mapping every legacy fragment to canonical utterances.
        Guarantees 100% coverage with zero unmapped records.
        """
        return build_legacy_crosswalk(legacy_turns, canonical_utterances)

    def compute_workload_metrics(
        self,
        legacy_count: int,
        review_items: List[ReviewItem],
        applied_decisions_count: int,
    ) -> Dict[str, Any]:
        """Calculates workload reduction achieved by migrating to grouped review units."""
        item_count = len(review_items)
        reduction_pct = round((1.0 - (item_count / max(1, legacy_count))) * 100.0, 2)

        return {
            "legacy_fragment_denominator": legacy_count,
            "proposed_review_items": item_count,
            "review_item_reduction_pct": reduction_pct,
            "historical_human_decisions_applied": applied_decisions_count,
            "workload_status": "QUALIFIED_REDUCTION" if reduction_pct >= 50.0 else "TARGET_MISSED",
        }
