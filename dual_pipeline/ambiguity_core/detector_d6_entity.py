"""Domain 6: Referenced Entity & Third-Party Stakeholder Clarification Detector.
Identifies uncontextualized name references in dialogue turns lacking authoritative bindings.
Enforces repository governance Constraints C-12 and C-27."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from dual_pipeline.parameters import (
    get_hitl_unresolved_entity_margin_threshold,
    needs_operator_decision,
)

from .d6_lexical_filter import (
    COMMUNICATIVE_VERBS,
    is_suppressed_or_common_word,
    is_valid_name_syntax,
    should_surface_unconfirmed_candidate,
)
from .models import (
    AmbiguityDomain,
    AmbiguityRecord,
    CandidateOption,
)

logger = logging.getLogger(__name__)

# Regex extractors for referenced actors in spoken dialogue (preposition 'what' eliminated under C-12)
MENTION_PATTERNS = [
    re.compile(r"\b(?:with|from|on|to|for|about|ask|tell|by)\s+([A-Z][a-z]{1,15})\b"),
    re.compile(
        r"\b([A-Z][a-z]{1,15})'s\s+(?:feedback|team|robot|approach|rules|threshold|call|input|thoughts|review|code)\b"
    ),
    re.compile(
        r"\b([A-Z][a-z]{1,15})\s+(?:was|said|says|agreed|suggested|mentioned|told|approved|signed|wanted|thinks|thought|stated|confirmed|noted)\b"
    ),
    re.compile(
        r"\b(?:bring|bringing|have|if)\s+([A-Z][a-z]{1,15})\s+(?:in|come|back|look|review)\b"
    ),
]

# Baseline directory for cross-referencing known enterprise identities
DEFAULT_ENTERPRISE_CANDIDATES: Dict[str, List[Dict[str, Any]]] = {
    "ed": [
        {
            "canonical_name": "Eduardo Juan More",
            "org": "Apex Global Enterprises (NEA)",
            "role": "Atlas Lead",
            "confidence": 0.65,
            "is_graph_grounded": True,
        },
        {
            "canonical_name": "Ed Moore",
            "org": "Apex Global Enterprises (NEA)",
            "role": "Rule Specialist",
            "confidence": 0.35,
            "is_graph_grounded": True,
        },
    ],
    "alex": [
        {
            "canonical_name": "Alex Sanchez",
            "org": "PartnerAdvisory",
            "role": "Tech Lead",
            "confidence": 0.60,
            "is_graph_grounded": True,
        },
        {
            "canonical_name": "Alexandra Coro",
            "org": "Apex Global Enterprises (NEA)",
            "role": "UAT Stakeholder",
            "confidence": 0.40,
            "is_graph_grounded": True,
        },
    ],
    "miles": [
        {
            "canonical_name": "Miles Erickson",
            "org": "PartnerAdvisory",
            "role": "Software Engineer",
            "confidence": 0.85,
            "is_graph_grounded": True,
        },
    ],
    "anthony": [
        {
            "canonical_name": "Parker Vance",
            "org": "Apex Global Enterprises (NEA / PGD)",
            "role": "Technical Director",
            "confidence": 0.95,
            "is_graph_grounded": True,
        },
    ],
    "clay": [
        {
            "canonical_name": "Clay (Former Architecture Lead)",
            "org": "Apex Global Enterprises / Contractor",
            "role": "Former Architecture Lead (AWS Strands)",
            "confidence": 0.70,
            "is_graph_grounded": True,
        },
    ],
    "josh": [
        {
            "canonical_name": "Josh (Engineering Reviewer)",
            "org": "Apex Global Enterprises (NEA)",
            "role": "Engineering Reviewer",
            "confidence": 0.65,
            "is_graph_grounded": True,
        },
    ],
    "lester": [
        {
            "canonical_name": "Lester (Domain Stakeholder)",
            "org": "Apex Global Enterprises (NEA)",
            "role": "Domain Stakeholder (Lens Thresholds)",
            "confidence": 0.60,
            "is_graph_grounded": True,
        },
    ],
    "karen": [
        {
            "canonical_name": "Morgan Vance Krutilla",
            "org": "PartnerAdvisory",
            "role": "LENS Team Contributor",
            "confidence": 0.95,
            "is_graph_grounded": True,
        },
    ],
    "karyn": [
        {
            "canonical_name": "Morgan Vance Krutilla",
            "org": "PartnerAdvisory",
            "role": "LENS Team Contributor",
            "confidence": 0.95,
            "is_graph_grounded": True,
        },
    ],
    "cora": [
        {
            "canonical_name": "Cora Duke-Botros",
            "org": "Apex Global Enterprises (NEA)",
            "role": "Domain Stakeholder",
            "confidence": 0.95,
            "is_graph_grounded": True,
        },
    ],
}


class EntityClarificationDetector:
    """Domain 6: In-dialogue referenced entity detector and HITL formulator."""

    def __init__(
        self,
        margin_threshold: Optional[float] = None,
        entity_registry: Optional[Dict[str, Any]] = None,
        identity_resolver: Any = None,
    ) -> None:
        self.margin_threshold = (
            margin_threshold
            if margin_threshold is not None
            else get_hitl_unresolved_entity_margin_threshold()
        )
        self.entity_registry = entity_registry or {}
        self._enterprise_directory = self._build_directory_from_resolver(identity_resolver)

    @staticmethod
    def _add_resolver_candidates(
        directory: Dict[str, List[Dict[str, Any]]],
        norm_first: str,
        candidates: List[Dict[str, Any]],
    ) -> None:
        existing = directory.setdefault(norm_first, [])
        existing_names = {e.get("canonical_name") for e in existing}
        for cand_entry in candidates:
            c_name = cand_entry.get("canonical_name", "")
            if not c_name:
                continue
            is_graph = "graph" in str(cand_entry.get("basis", "")).lower()
            conf = 0.95 if is_graph else 0.80
            from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
                strip_org_suffix_from_role,
            )
            raw_role = cand_entry.get("role") or "Stakeholder"
            clean_role = strip_org_suffix_from_role(raw_role)
            if " / " in clean_role:
                clean_role = clean_role.split(" / ")[0].strip()

            entry = {
                "canonical_name": c_name,
                "org": cand_entry.get("organization") or "Unknown",
                "role": clean_role,
                "confidence": conf,
                "is_graph_grounded": True,
            }
            if c_name in existing_names:
                if is_graph:
                    for idx, ex in enumerate(existing):
                        if ex.get("canonical_name") == c_name:
                            existing[idx] = entry
            else:
                if is_graph:
                    existing.insert(0, entry)
                else:
                    existing.append(entry)
                existing_names.add(c_name)

    @classmethod
    def _build_directory_from_resolver(
        cls,
        identity_resolver: Any,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Build the candidate directory from resolver.first_name_index."""
        directory: Dict[str, List[Dict[str, Any]]] = {
            k: list(v) for k, v in DEFAULT_ENTERPRISE_CANDIDATES.items()
        }
        resolver = identity_resolver
        if resolver is None:
            try:
                from dual_pipeline.identity_resolver_core.resolver import IdentityResolver
                resolver = IdentityResolver()
            except Exception:
                return directory
        if resolver is None:
            return directory
        for norm_first, candidates in resolver.first_name_index.items():
            if norm_first and len(norm_first) >= 2:
                cls._add_resolver_candidates(directory, norm_first, candidates)
        return directory

    def _normalize_name_token(self, token: str) -> str:
        return token.strip().lower()

    def _is_known_roster_attendee(self, token: str, attendees: List[str]) -> bool:
        norm = self._normalize_name_token(token)
        for att in attendees:
            att_norm = self._normalize_name_token(att)
            parts = [re.sub(r"[^\w]", "", p) for p in att_norm.split()]
            if norm == att_norm or norm in parts:
                return True
        return False

    def _build_turn_directory(
        self,
        known_entities: Optional[List[Dict[str, Any]]],
        cross_meeting_registry: Optional[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        directory = {k: list(v) for k, v in self._enterprise_directory.items()}
        for k, val in (cross_meeting_registry or {}).items():
            norm_k = self._normalize_name_token(k)
            directory.setdefault(norm_k, []).insert(0, val)
        for ent in (known_entities or []):
            name = ent.get("canonical_name") or ent.get("display_name") or ""
            if name:
                directory.setdefault(name.split()[0].lower(), []).insert(
                    0,
                    {
                        "canonical_name": name,
                        "org": ent.get("organization") or "Unknown",
                        "role": ent.get("role") or "Stakeholder",
                        "confidence": 0.90,
                        "is_graph_grounded": True,
                    },
                )
        return directory

    def _prescan_turns(
        self, turns: List[Dict[str, Any]]
    ) -> tuple[Dict[str, int], Dict[str, bool]]:
        token_turn_counts: Dict[str, int] = {}
        token_has_relational: Dict[str, bool] = {}
        for turn in turns:
            text = turn.get("text", "")
            for norm in self._scan_turn_evidence(text, token_has_relational):
                token_turn_counts[norm] = token_turn_counts.get(norm, 0) + 1
        return token_turn_counts, token_has_relational

    def _iter_pattern_matches(self, text: str) -> List[tuple[str, re.Match]]:
        if not text:
            return []
        hits: List[tuple[str, re.Match]] = []
        for pattern in MENTION_PATTERNS:
            for match in pattern.finditer(text):
                token = match.group(1).strip()
                if token and not is_suppressed_or_common_word(token):
                    hits.append((token, match))
        return hits

    def _scan_turn_evidence(
        self, text: str, token_has_relational: Dict[str, bool]
    ) -> Set[str]:
        turn_seen: Set[str] = set()
        for token, match in self._iter_pattern_matches(text):
            norm = self._normalize_name_token(token)
            turn_seen.add(norm)
            preceding = text[max(0, match.start() - 30) : match.start()]
            ctx_to_check = (preceding + " " + match.group(0)).lower()
            if any(v in ctx_to_check for v in COMMUNICATIVE_VERBS):
                token_has_relational[norm] = True
        return turn_seen

    def _extract_turn_tokens(
        self, text: str, directory: Dict[str, List[Dict[str, Any]]]
    ) -> Set[str]:
        from dual_pipeline.transcript_qa_core.gate1_entity_context import (
            is_product_homophone_context,
        )

        extracted: Set[str] = set()
        for token, match in self._iter_pattern_matches(text):
            if is_product_homophone_context(text, token):
                continue
            norm_token = self._normalize_name_token(token)
            preceding = text[max(0, match.start() - 30) : match.start()]
            if is_valid_name_syntax(
                token, preceding, text, is_in_directory=(norm_token in directory)
            ):
                extracted.add(token)
        return extracted

    def _lookup_known_candidates(
        self,
        token: str,
        norm_token: str,
        directory: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        if norm_token in directory:
            return list(directory[norm_token])
        from dual_pipeline.transcript_metadata_core.entity_resolver import (
            resolve_person_entity,
        )

        ent = resolve_person_entity(token)
        if not ent or getattr(ent, "source", "") == "enterprise_directory":
            return []
        if getattr(ent, "candidates", None):
            return [
                {
                    "canonical_name": ent.canonical_name,
                    "org": ent.org,
                    "role": cand.role,
                    "confidence": float(cand.confidence),
                    "is_graph_grounded": True,
                    "assertion_id": getattr(cand, "assertion_id", ""),
                }
                for cand in ent.candidates
            ]
        if ent.canonical_name and ent.role:
            return [
                {
                    "canonical_name": ent.canonical_name,
                    "org": ent.org,
                    "role": ent.role,
                    "confidence": float(ent.confidence),
                    "is_graph_grounded": True,
                }
            ]
        return []

    def _dedupe_candidates(
        self, token: str, known_cands: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], bool]:
        deduped: Dict[str, Dict[str, Any]] = {}
        is_graph_grounded = False
        for c in known_cands:
            c_name = c.get("canonical_name", token).strip()
            cand_key = f"{self._normalize_name_token(c_name)}:{str(c.get('role', '')).strip().lower()}"
            if c.get("is_graph_grounded") or c.get("org") not in ("Unknown", "Unknown Org"):
                is_graph_grounded = True
            self._merge_candidate_entry(deduped, cand_key, c)
        return list(deduped.values()), is_graph_grounded

    @staticmethod
    def _merge_candidate_entry(
        deduped: Dict[str, Dict[str, Any]], cand_key: str, cand: Dict[str, Any]
    ) -> None:
        if cand_key not in deduped:
            deduped[cand_key] = dict(cand)
            return
        existing = deduped[cand_key]
        unverified_orgs = ("Verified Speaker", "Unknown", "Unknown Org")
        if cand.get("is_graph_grounded") or (
            existing.get("org") in unverified_orgs and cand.get("org") not in unverified_orgs
        ):
            deduped[cand_key] = dict(cand)
        elif float(cand.get("confidence", 0.0)) > float(existing.get("confidence", 0.0)):
            existing["confidence"] = cand.get("confidence")

    def _build_candidate_options(
        self,
        token: str,
        norm_token: str,
        unique_cands: List[Dict[str, Any]],
        is_graph_grounded: bool,
        freq: int,
        rel: bool,
    ) -> List[CandidateOption]:
        if unique_cands:
            return self._options_from_known_cands(token, norm_token, unique_cands, is_graph_grounded)
        if not should_surface_unconfirmed_candidate(
            token, turn_frequency=freq, has_relational_verb=rel
        ):
            return []
        return self._options_for_unconfirmed_token(token, norm_token)

    @staticmethod
    def _options_from_known_cands(
        token: str,
        norm_token: str,
        unique_cands: List[Dict[str, Any]],
        is_graph_grounded: bool,
    ) -> List[CandidateOption]:
        candidates: List[CandidateOption] = []
        for i, cand in enumerate(unique_cands):
            c_name = cand.get("canonical_name", token)
            c_org = cand.get("org", "Unknown Org")
            c_role = cand.get("role", "Unknown Role")
            candidates.append(
                CandidateOption(
                    option_id=f"opt-{norm_token}-{i + 1}",
                    text=f"{c_name} ({c_org}, {c_role})",
                    confidence=float(cand.get("confidence", 0.50)),
                    rationale=f"Graph/Directory Match: Identified as {c_name} at {c_org}.",
                    is_recommended=(i == 0),
                    attributes={
                        "canonical_name": c_name,
                        "org": c_org,
                        "role": c_role,
                        "token": token,
                        "is_graph_grounded": cand.get("is_graph_grounded", is_graph_grounded),
                    },
                )
            )
        if len(candidates) == 1 and not is_graph_grounded:
            ext_conf = round(max(0.15, 1.0 - candidates[0].confidence), 2)
            candidates.append(
                CandidateOption(
                    option_id=f"opt-{norm_token}-external",
                    text=f"External / Unconfirmed Stakeholder ({token})",
                    confidence=ext_conf,
                    rationale="Person not registered in Knowledge Graph; treat as external stakeholder.",
                    is_recommended=False,
                    attributes={"token": token, "canonical_name": token, "is_graph_grounded": False},
                )
            )
        return candidates

    @staticmethod
    def _options_for_unconfirmed_token(token: str, norm_token: str) -> List[CandidateOption]:
        return [
            CandidateOption(
                option_id=f"opt-{norm_token}-clarify",
                text=f"{token} (Organization and Role Unconfirmed)",
                confidence=0.50,
                rationale=f"In-dialogue reference to '{token}' with positive evidence but no Knowledge Graph match.",
                is_recommended=True,
                attributes={"canonical_name": token, "token": token, "is_graph_grounded": False},
            ),
            CandidateOption(
                option_id=f"opt-{norm_token}-unassigned",
                text=f"External / Anonymous Third-Party ({token})",
                confidence=0.50,
                rationale="Treat as non-roster third-party mention.",
                is_recommended=False,
                attributes={"token": token, "canonical_name": token, "is_graph_grounded": False},
            ),
        ]

    def _finalize_record(
        self,
        norm_token: str,
        idx: int,
        text: str,
        candidates: List[CandidateOption],
        is_graph_grounded: bool,
    ) -> AmbiguityRecord:
        total_conf = sum(c.confidence for c in candidates)
        if total_conf > 0:
            for c in candidates:
                c.confidence = round(c.confidence / total_conf, 2)
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        top_conf = candidates[0].confidence if candidates else 0.0
        second_conf = candidates[1].confidence if len(candidates) > 1 else 0.0
        delta = round(top_conf - second_conf, 2)
        is_ambiguous = needs_operator_decision(
            top_conf, second_conf, threshold=self.margin_threshold
        )
        requires_hitl = (
            (not is_graph_grounded) or is_ambiguous or (len(candidates) >= 2 and delta < 0.35)
        )
        return AmbiguityRecord(
            ambiguity_id=f"amb-d6-{norm_token}-{idx}",
            domain=AmbiguityDomain.D6_ENTITY_CLARIFICATION,
            segment_id=f"turn_{idx}",
            time_span_ms=(0, 0),
            source_excerpt=f"...{text[:120]}...",
            candidates=candidates,
            delta_margin=delta,
            requires_hitl=requires_hitl,
            is_material=True,
        )

    def _process_turn_tokens(
        self,
        idx: int,
        text: str,
        attendees: List[str],
        seen_tokens: Set[str],
        directory: Dict[str, List[Dict[str, Any]]],
        token_turn_counts: Dict[str, int],
        token_has_relational: Dict[str, bool],
    ) -> List[AmbiguityRecord]:
        turn_records: List[AmbiguityRecord] = []
        for token in self._extract_turn_tokens(text, directory):
            norm_token = self._normalize_name_token(token)
            if norm_token in seen_tokens or self._is_known_roster_attendee(token, attendees):
                continue
            seen_tokens.add(norm_token)
            known_cands = self._lookup_known_candidates(token, norm_token, directory)
            unique_cands, is_graph_grounded = self._dedupe_candidates(token, known_cands)
            candidates = self._build_candidate_options(
                token,
                norm_token,
                unique_cands,
                is_graph_grounded,
                token_turn_counts.get(norm_token, 1),
                token_has_relational.get(norm_token, False),
            )
            if candidates:
                turn_records.append(
                    self._finalize_record(norm_token, idx, text, candidates, is_graph_grounded)
                )
        return turn_records

    def detect(
        self,
        turns: List[Dict[str, Any]],
        known_attendees: Optional[List[str]] = None,
        known_entities: Optional[List[Dict[str, Any]]] = None,
        cross_meeting_registry: Optional[Dict[str, Any]] = None,
    ) -> List[AmbiguityRecord]:
        """Scans transcript turns for referenced entities requiring clarification."""
        attendees = known_attendees or []
        records: List[AmbiguityRecord] = []
        seen_tokens: Set[str] = set()
        directory = self._build_turn_directory(known_entities, cross_meeting_registry)
        token_turn_counts, token_has_relational = self._prescan_turns(turns)

        for idx, turn in enumerate(turns):
            text = turn.get("text", "")
            if text:
                records.extend(
                    self._process_turn_tokens(
                        idx,
                        text,
                        attendees,
                        seen_tokens,
                        directory,
                        token_turn_counts,
                        token_has_relational,
                    )
                )

        return records
