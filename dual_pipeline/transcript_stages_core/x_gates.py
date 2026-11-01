"""X-Gates — Exit Evaluator (X1–X3)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.transcript_models import CanonicalSignalProjectionV57

from .constants import _PASSIVE_VERBS

logger = logging.getLogger(__name__)


def _parse_transcript_turns(transcript: str) -> List[Dict[str, Any]]:
    """Parses transcript into structured turns with char offsets, timestamp, speaker, and dialogue."""
    if not transcript:
        return []
    header_pattern = re.compile(
        r"(?:^|\n)(?:###\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*\n\n)?\*\*([^*]+?):\*\*\s*(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?",
        re.MULTILINE,
    )
    matches = list(header_pattern.finditer(transcript))
    if not matches:
        return [{
            "timestamp": "",
            "speaker": "",
            "dialogue": transcript,
            "start": 0,
            "end": len(transcript),
        }]
    turns: List[Dict[str, Any]] = []
    for i, m in enumerate(matches):
        ts = m.group(1) or m.group(3) or ""
        spk = m.group(2).strip()
        d_start = m.end()
        d_end = matches[i + 1].start() if i + 1 < len(matches) else len(transcript)
        dialogue = transcript[d_start:d_end].strip()
        turns.append({
            "timestamp": ts,
            "speaker": spk,
            "dialogue": dialogue,
            "start": d_start,
            "end": d_end,
        })
    return turns


def _find_best_verbatim_span(
    turn_dialogue: str,
    target_text: str,
    min_chars: int = 15,
) -> Optional[str]:
    """Finds the longest contiguous verbatim span (>= min_chars) in turn_dialogue matching target_text words."""
    if not turn_dialogue or not target_text:
        return None

    # 1. Check if full target_text or any clause in target_text exists verbatim
    if target_text in turn_dialogue and len(target_text) >= min_chars:
        return target_text

    raw_clauses = [c.strip() for c in re.split(r"[,;.\n!?-]+\s*", target_text) if len(c.strip()) >= min_chars]
    for c in sorted(raw_clauses, key=len, reverse=True):
        if c in turn_dialogue:
            return c

    # 2. Extract words from target_text
    tw = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9]+\b", target_text)]
    if not tw:
        return None
    tw_set = set(tw)

    # 3. Check sentences in turn_dialogue
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", turn_dialogue) if len(s.strip()) >= min_chars]
    scored_sentences = []
    for s in sentences:
        s_words = set(re.findall(r"\b[a-zA-Z0-9]+\b", s.lower()))
        overlap = len(tw_set & s_words)
        if overlap >= 2:
            scored_sentences.append((overlap, len(s), s))
    if scored_sentences:
        scored_sentences.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return scored_sentences[0][2]

    # 4. Longest common contiguous word subsegment via DP
    tokens = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\b[a-zA-Z0-9]+\b", turn_dialogue)]
    if not tokens:
        return None
    words_a = [t[0].lower() for t in tokens]
    max_len = 0
    end_i = -1
    dp = [0] * (len(tw) + 1)
    for i, wa in enumerate(words_a):
        new_dp = [0] * (len(tw) + 1)
        for j, wb in enumerate(tw):
            if wa == wb:
                new_dp[j + 1] = dp[j] + 1
                if new_dp[j + 1] > max_len:
                    max_len = new_dp[j + 1]
                    end_i = i
        dp = new_dp

    if max_len >= 2 and end_i >= 0:
        start_i = end_i - max_len + 1
        cand_span = turn_dialogue[tokens[start_i][1] : tokens[end_i][2]].strip()
        if len(cand_span) >= min_chars:
            return cand_span

    # 5. Fallback: longest clause in turn_dialogue with any word overlap
    clauses = [c.strip() for c in re.split(r"[,;.\n!?-]+\s*", turn_dialogue) if len(c.strip()) >= min_chars]
    scored_clauses = []
    for c in clauses:
        c_words = set(re.findall(r"\b[a-zA-Z0-9]+\b", c.lower()))
        overlap = len(tw_set & c_words)
        if overlap >= 1:
            scored_clauses.append((overlap, len(c), c))
    if scored_clauses:
        scored_clauses.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return scored_clauses[0][2]

    return None


class XExitGatesEvaluator:
    """X1-X3 exit gates: validates output artifacts against Pydantic schemas
    and cross-output coherence constraints."""

    def execute(
        self,
        recap: str,
        graph_json: str,
        metadata: Dict[str, Any],
        transcript: str = "",
        auto_heal: Optional[bool] = None,
    ) -> bool:
        logger.info("Executing X1-X3: Exit Gates")
        data = self._validate_graph_schema(graph_json)
        is_document = bool(
            metadata.get("is_document")
            or metadata.get("document")
            or metadata.get("canonical_modality") == "DOCUMENT"
            or (metadata.get("output3_only") and not transcript and not recap)
        )
        should_heal = auto_heal if auto_heal is not None else bool(metadata.get("auto_heal", False))
        if not is_document:
            if should_heal:
                from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
                heal_meta = {k: v for k, v in (metadata or {}).items() if k != "state_machine"}
                recap, recap_ch = DeterministicHealer.heal_output_2(recap, metadata=heal_meta, output_1=transcript)
                if recap_ch or not DeterministicHealer._is_frozen(metadata or {}, "output_2"):
                    self._persist_healed_recap(recap, metadata)
                self._heal_cross_output_coherence(data, metadata, transcript=transcript)
            self._validate_anti_conflation(data, recap)
            self._validate_graph_sprawl(
                data.get("graph_mutations", {}),
                metadata,
                evidence_only_statement_ids=data.get("evidence_only_statement_ids", []),
            )
            self._validate_recap_structure(recap)
            self._validate_cross_output_coherence(data, metadata, transcript=transcript, recap=recap)
            if should_heal:
                if not DeterministicHealer._is_frozen(metadata or {}, "output_3"):
                    self._persist_healed_graph(data, metadata)
        else:
            from .anti_conflation import validate_anti_conflation
            validate_anti_conflation(data, context_label="Artifact 03 graph projection")
            self._validate_graph_sprawl(
                data.get("graph_mutations", {}),
                metadata,
                evidence_only_statement_ids=data.get("evidence_only_statement_ids", []),
            )
        return True

    def _validate_anti_conflation(self, data: Dict[str, Any], recap: str) -> None:
        from .anti_conflation import validate_anti_conflation
        validate_anti_conflation(data, context_label="Artifact 03 graph projection")
        validate_anti_conflation(recap, context_label="Artifact 02 executive recap")

    def _validate_graph_schema(self, graph_json: str) -> Dict[str, Any]:
        try:
            data = json.loads(graph_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"X1 FAIL: Invalid JSON in graph output: {e}")

        if data.get("schema_version") != "5.7":
            raise ValueError("X1 FAIL: Graph delta does not meet schema_version 5.7 constraint")

        try:
            CanonicalSignalProjectionV57.model_validate(data)
        except Exception as e:
            raise ValueError(f"X1 FAIL: Graph delta does not conform to CanonicalSignalProjectionV57: {e}")
        return data

    def _validate_graph_sprawl(
        self,
        mutations: Dict[str, Any],
        metadata: Dict[str, Any],
        evidence_only_statement_ids: Optional[List[str]] = None,
    ) -> None:
        self._validate_entity_uniqueness(mutations.get("entity_upserts", []))
        self._validate_edge_uniqueness(mutations.get("relationship_signals", []))
        self._validate_assertion_uniqueness(mutations)
        self._validate_prior_context(mutations.get("entity_upserts", []), metadata)
        if evidence_only_statement_ids is not None:
            promoted_mutation_count = sum(
                len(mutations.get(cat, []))
                for cat in ("decision_updates", "proposal_updates", "action_updates", "risk_updates")
            )
            max_allowed = max(3, 2 * promoted_mutation_count)
            if len(evidence_only_statement_ids) > max_allowed:
                raise ValueError(
                    f"X1 FAIL: Rule C-17 violation: evidence_only_statement_ids count ({len(evidence_only_statement_ids)}) "
                    f"exceeds max(3, 2 * promoted_mutation_count) = {max_allowed}."
                )

    def _validate_entity_uniqueness(self, entity_upserts: list) -> None:
        seen_entity_ids = set()
        for ent in entity_upserts:
            eid = ent.get("id")
            if eid in seen_entity_ids:
                raise ValueError(f"X1 FAIL: Duplicate entity ID '{eid}' in entity_upserts (graph sprawl violation).")
            seen_entity_ids.add(eid)

    def _validate_edge_uniqueness(self, relationship_signals: list) -> None:
        seen_edge_triples = set()
        for rel in relationship_signals:
            triple = (rel.get("from_id"), rel.get("relationship"), rel.get("to_id"))
            if triple in seen_edge_triples:
                raise ValueError(f"X1 FAIL: Duplicate relationship edge {triple} in relationship_signals (graph sprawl violation).")
            seen_edge_triples.add(triple)

    def _validate_assertion_uniqueness(self, mutations: Dict[str, Any]) -> None:
        categories = [
            ("decision_updates", "decision_id"),
            ("proposal_updates", "proposal_id"),
            ("action_updates", "action_id"),
            ("risk_updates", "risk_id"),
        ]
        for cat, id_attr in categories:
            seen_ids = set()
            for item in mutations.get(cat, []):
                aid = item.get(id_attr)
                if aid in seen_ids:
                    raise ValueError(f"X1 FAIL: Duplicate assertion ID '{aid}' in {cat} (graph sprawl violation).")
                seen_ids.add(aid)

    def _validate_prior_context(self, entity_upserts: list, metadata: Dict[str, Any]) -> None:
        prior_entity_ids = set(metadata.get("prior_entity_ids", []))
        for ent in entity_upserts:
            eid = ent.get("id")
            mut = ent.get("mutation")
            if eid in prior_entity_ids and mut == "CREATE":
                raise ValueError(
                    f"X1 FAIL: Entity '{eid}' exists in prior context but declared mutation 'CREATE'. "
                    f"Updates must overwrite via 'CONFIRM', 'MODIFY', or 'SUPERSEDE' (anti-sprawl violation)."
                )

    def _validate_recap_structure(self, recap: str) -> None:
        if not re.search(r"#{1,3}\s+(?:\d+\.\s*)?(?:Meeting Objective|Overview)\b", recap, re.IGNORECASE):
            raise ValueError("X2 FAIL: Recap missing 'Meeting Objective' section")
        if not re.search(r"#{1,3}\s+(?:\d+\.\s*)?(?:Action\s+&\s+Governance\s+Ledger|Next Steps|Action Items)", recap):
            raise ValueError("X2 FAIL: Recap missing 'Action & Governance Ledger' section")

        if re.search(r"\bSpeaker\s+[A-Z]\b", recap):
            raise ValueError("X2 FAIL: Anti-embarrassment gate triggered. Unresolved 'Speaker [A-Z]' found in recap.")
        if re.search(r"Organization\s+not\s+specified", recap, re.IGNORECASE):
            raise ValueError("X2 FAIL: Anti-embarrassment gate triggered. 'Organization not specified' found in attendees.")

        self._validate_action_items(recap)

    def _validate_action_items(self, recap: str) -> None:
        owner_col_idx = 3
        action_col_idx = 2
        timing_col_idx = 4
        data_row_count = 0
        named_dri_count = 0
        forbidden_owners = {
            "not assigned", "tbc", "tbd", "unknown", "unassigned",
            "pending owner", "pending", "team", "all", "n/a", "none",
        }
        for line in recap.split("\n"):
            clean = line.strip()
            if not clean.startswith("|"):
                continue
            cols = [c.strip() for c in clean.split("|")]
            lower_cols = [c.lower() for c in cols]
            is_data_row = bool(re.match(r"^(?:[A-Za-z]+[-_]?\d+\.?|\d+\.?)$", cols[1] if len(cols) > 1 else ""))
            if not is_data_row and any("owner" in c or "accountable" in c or "dri" in c for c in lower_cols):
                for idx, c in enumerate(lower_cols):
                    if "owner" in c or "accountable" in c or "dri" in c:
                        owner_col_idx = idx
                    elif "action" in c or "deliverable" in c or "item" in c:
                        action_col_idx = idx
                    elif "timing" in c or "due" in c or "target" in c or "date" in c or "milestone" in c:
                        timing_col_idx = idx
                continue
            if all(not c or re.match(r"^[-:\s]+$", c) for c in cols):
                continue
            if not is_data_row:
                continue
            if len(cols) <= max(action_col_idx, owner_col_idx):
                continue
            data_row_count += 1
            action_text = cols[action_col_idx]
            owner_text = cols[owner_col_idx]
            timing_text = cols[timing_col_idx] if len(cols) > timing_col_idx else ""

            if _PASSIVE_VERBS.match(action_text):
                raise ValueError(f"X2 FAIL: Anti-embarrassment gate triggered. Passive action detected: '{action_text}'")

            owner_clean = owner_text.strip().lower()
            timing_clean = timing_text.strip().lower()

            # Rule C-25: "Unassigned (Pending Owner Assignment)" is permitted placeholder
            is_c25_pending = owner_clean == "unassigned (pending owner assignment)"
            if not is_c25_pending and (
                owner_clean in forbidden_owners
                or owner_clean.startswith("unassigned")
                or owner_clean.startswith("pending owner")
                or bool(re.match(r"^Speaker\s+[A-Z0-9]+$", owner_text, re.IGNORECASE))
            ):
                raise ValueError(f"X2 FAIL: Anti-embarrassment gate triggered. Action item owner '{owner_text}' is not a resolved person.")

            if timing_col_idx is not None and len(cols) > timing_col_idx:
                status_text = cols[6].strip().lower() if len(cols) > 6 else ""
                is_proposed = "proposed" in status_text or "proposed" in timing_clean
                if not is_c25_pending and not is_proposed and (
                    timing_clean in ("tbd", "tbd - unspecified", "unspecified", "pending", "unscheduled", "n/a", "none", "unknown", "---", "-")
                    or timing_clean.startswith("tbd")
                ):
                    raise ValueError(
                        f"X2 FAIL: Anti-embarrassment gate triggered. Action item target timing '{timing_text}' is ungrounded TBD."
                    )
            named_dri_count += 1

        if data_row_count > 0 and named_dri_count == 0:
            raise ValueError(
                "X2 FAIL: Anti-embarrassment gate triggered. Action & Governance Ledger contains no named accountable owners. "
                "Emit the clean confirmation sentinel if no formal actions were agreed."
            )

    def _validate_cross_output_coherence(
        self,
        data: Dict[str, Any],
        metadata: Dict[str, Any],
        transcript: str = "",
        recap: str = "",
    ) -> None:
        from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
            MeetingXExitContract,
        )
        ok, errs = MeetingXExitContract.validate_x3_cross_output_coherence(
            f1_text=transcript,
            f2_text=recap,
            f3_data=data,
            metadata=metadata,
        )
        if not ok:
            raise ValueError(f"X3 FAIL: Cross-output coherence violation: {'; '.join(errs)}")
        self._validate_speaker_attribution_lineage(data, metadata, transcript=transcript, recap=recap)
        logger.info("X3: Cross-output coherence check passed")

    def _validate_speaker_attribution_lineage(
        self,
        data: Dict[str, Any],
        metadata: Dict[str, Any],
        transcript: str = "",
        recap: str = "",
    ) -> None:
        """Blocks Output 03 decisions, actions, or person role/ownership edges bound to undiarized or unresolved ALR speakers."""
        ev_index = data.get("evidence_index") or {}
        mutations = data.get("graph_mutations") or {}
        unverified_re = re.compile(
            r"^(?:speaker[_\s]*\d+|speaker[_\s]+[a-z]|voice[_\s]+[a-z0-9]+|unresolved.*|undiarized_quarantined)$",
            re.IGNORECASE,
        )
        unresolved_alr_speakers: set[str] = set()
        for m in re.finditer(r"\*\*UNRESOLVED\s*(?:—|--|-)\s*([^*]+)\*\*", transcript or ""):
            unresolved_alr_speakers.add(m.group(1).strip().lower())

        bad_ev_ids: set[str] = set()
        for eid, ev in ev_index.items():
            if not isinstance(ev, dict):
                continue
            spk = str(ev.get("speaker") or "").strip()
            tier = str(ev.get("attribution_provenance_tier") or "").upper()
            if (
                unverified_re.fullmatch(spk)
                or spk.lower() in unresolved_alr_speakers
                or tier in ("UNVERIFIED", "UNDIARIZED_QUARANTINED")
            ):
                bad_ev_ids.add(eid)

        if not bad_ev_ids and not unresolved_alr_speakers:
            return

        for bucket in ("decision_updates", "action_updates"):
            for item in mutations.get(bucket, []):
                if not isinstance(item, dict):
                    continue
                eids = item.get("evidence_ids") or []
                if eids and all(e in bad_ev_ids for e in eids):
                    raise ValueError(
                        f"X3 FAIL: {bucket} item '{item.get('decision_id') or item.get('action_id')}' "
                        f"is anchored to undiarized or unresolved speaker evidence {eids}."
                    )
        for sig in mutations.get("relationship_signals", []):
            if not isinstance(sig, dict):
                continue
            rel = str(sig.get("relationship") or sig.get("predicate") or "").upper()
            fid = str(sig.get("from_id") or "")
            eids = sig.get("evidence_ids") or []
            if fid.startswith("person:") and rel in ("HAS_ROLE", "OWNS", "DELIVERS"):
                if eids and all(e in bad_ev_ids for e in eids):
                    raise ValueError(
                        f"X3 FAIL: Person relationship '{fid} -> {rel}' is anchored to undiarized or unverified speaker evidence {eids}."
                    )

    def _heal_cross_output_coherence(
        self,
        data: Dict[str, Any],
        metadata: Dict[str, Any],
        transcript: str = "",
    ) -> None:
        """Deprecated no-op: XExitGatesEvaluator is strictly read-only and must not mutate graph payloads pre-gate."""
        return

    def _reanchor_span(
        self,
        ev_item: Dict[str, Any],
        exact_text: str,
        turns: List[Dict[str, Any]],
        transcript: str,
    ) -> Optional[str]:
        """Locates matching turn and extracts best verbatim span >= 15 chars."""
        loc = ev_item.get("locator")
        loc_val = str(loc.get("value") if isinstance(loc, dict) else (loc or "")).strip()
        spk = str(ev_item.get("speaker") or "").lower()
        spk_tokens = {p for p in re.findall(r"[a-z0-9]+", spk) if p not in ("person", "speaker", "dr", "mr", "ms", "true", "false")}
        tw = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", exact_text.lower()))

        scored_turns: List[Tuple[int, Dict[str, Any]]] = []
        for t in turns:
            score = 0
            if loc_val and t.get("timestamp"):
                t_ts = t["timestamp"].lstrip("0:")
                l_ts = loc_val.lstrip("0:")
                if t_ts == l_ts or (t_ts and l_ts and (t_ts in l_ts or l_ts in t_ts)):
                    score += 50
            if spk_tokens:
                turn_spk_tokens = set(re.findall(r"[a-z0-9]+", t.get("speaker", "").lower()))
                if spk_tokens & turn_spk_tokens:
                    score += 25
            if tw:
                turn_tw = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", t.get("dialogue", "").lower()))
                score += len(tw & turn_tw) * 3
            if score > 0:
                scored_turns.append((score, t))

        scored_turns.sort(key=lambda x: x[0], reverse=True)
        for _, cand_turn in scored_turns:
            span = _find_best_verbatim_span(cand_turn["dialogue"], exact_text, min_chars=15)
            if span and span in transcript:
                return span

        span = _find_best_verbatim_span(transcript, exact_text, min_chars=15)
        if span and span in transcript:
            return span
        return None

    def _heal_quote_pointers(self, data: Dict[str, Any], transcript: str = "") -> None:
        from dual_pipeline.transcript_qa_core.quote_pointer_healer import heal_quote_pointers
        heal_quote_pointers(data, transcript=transcript)

    def _heal_roster_entities(self, data: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        """Deprecated no-op: roster entities must be extracted upstream without hardcoding PartnerAdvisory affiliation."""
        return

    def _persist_healed_recap(self, recap: str, metadata: Dict[str, Any]) -> None:
        """Read-only exit gate invariant: XExitGatesEvaluator never mutates files on disk."""
        return

    def _persist_healed_graph(self, data: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        """Read-only exit gate invariant: XExitGatesEvaluator never mutates files on disk."""
        return

