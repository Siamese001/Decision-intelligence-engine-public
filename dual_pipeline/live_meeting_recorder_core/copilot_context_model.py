"""
dual_pipeline/live_meeting_recorder_core/copilot_context_model.py

Deterministic, provider-free, two-layer conversation context model for Live Copilot and CLI advisory.
Replaces legacy sliding-window buffers with:
  1. Focus Layer: Recent turns within focus_seconds at full weight (1.0).
  2. Decay Layer: Exponentially decaying moving average (EWMA) of entity and topic salience across the whole meeting.
  3. Flow State: Structured running state tracking topics, open questions, commitments, decisions, objections/risks,
     and speaker turn-taking dynamics.
  4. Conversational Act Detection: Deterministic classification of current dialogue act.
  5. Retrieval Seeds: High-salience blended seeds for GraphDB context retrieval.
  6. Career Firewall: Rule C-19 quarantine enforcement ensuring C-19 markers never enter seeds or flow state.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import EpisodeTracker, ensure_inferred_memory_boundary
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import has_c19_marker
from dual_pipeline.parameters import (
    get_copilot_context_decay_tau_seconds, get_copilot_context_flow_items_max,
    get_copilot_context_focus_seconds, get_copilot_context_focus_weight, get_copilot_context_salient_entities_top_k,
)

# Canonical conversational acts
CANONICAL_ACTS = ("question_to_operator", "objection", "decision_point", "commitment", "topic_shift", "status_update")

# Fast entity extraction regex patterns
ACRONYM_PATTERN = re.compile(r"\b(?=[A-Z0-9]{2,6}\b)(?![0-9]+$)[A-Z0-9]+\b")
PROPER_NOUN_PATTERN = re.compile(r"\b[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*){0,5}\b")
ENTITY_ID_PATTERN = re.compile(
    r"\b(?:person|org|system|workstream|project|metric|role|policy):[a-zA-Z0-9_\-]+\b",
    re.IGNORECASE,
)

# Common stopwords to exclude from proper noun matches
STOPWORDS: Set[str] = {
    "A", "An", "The", "This", "That", "These", "Those", "We", "They", "You", "He", "She", "It", "What",
    "When", "Where", "How", "Why", "Who", "Whom", "Which", "Yes", "No", "Let", "Let's", "If", "So", "And",
    "Or", "But", "Our", "My", "Your", "Their", "His", "Her", "Its", "As", "Currently", "Moving", "Changing",
    "Next", "On", "There", "Can", "Could", "Is", "Are", "Was", "Were", "Should", "Would", "Will", "I", "I'll",
    "I'm", "I've", "We'll", "We've", "Don't", "Can't", "Won't", "Not", "Just", "Also", "Then", "Here", "Now",
    "All", "Some", "Any", "Both", "Each", "Few", "More", "Most", "Other", "Such", "Than", "Too", "Very",
    "Only", "Same", "At", "By", "For", "From", "In", "Into", "Of", "Off", "Onto", "Out", "Over", "To", "Up",
    "With", "Continuing", "Regarding", "Reviewing", "Discussing", "Looking", "Starting", "Following", "Adding",
    "Using", "Checking", "Having", "Getting", "Taking", "Making", "Going", "Team", "Meeting", "Discussion",
    "Back", "Thank", "Thanks", "Like", "Right", "Yeah", "Discuss", "Well", "Sure", "Okay", "Ok", "Good",
    "Great", "Nice", "Cool", "Fine", "Look", "See", "Think", "Thought", "Know", "Mean", "Said", "Say",
    "Saying", "Talk", "Talking", "Call", "Called", "Week", "Today", "Yesterday", "Tomorrow", "Last", "First",
    "Second", "Third", "One", "Two", "Three", "Four", "Five", "Turn", "People", "Person", "Somebody",
    "Someone", "Something", "Anything", "Everything", "Nothing", "Actually", "Basically", "Probably", "Maybe",
    "Perhaps", "Really", "Still", "Even", "Much", "Little", "Lot", "Bit", "Kind", "Sort", "Part", "Point",
    "Way", "Time", "Day", "BRD", "FW", "POV", "SW", "OH", "UH", "UM", "AH", "DO", "GO", "US", "ME", "AM", "BE",
    "Sometimes", "Usually", "Often", "Always", "Never", "However", "Therefore", "Otherwise", "Meanwhile",
    "Instead", "Besides", "Anyway", "Overall", "Especially", "Particularly", "Specifically", "Generally",
    "Typically", "Honestly", "Literally", "Obviously", "Clearly", "Definitely", "Certainly", "Absolutely",
    "Totally", "Exactly", "Yep", "Nope", "Plus", "Though", "Although", "Because", "Since", "While", "Unless",
    "Until", "Before", "After", "During", "Within", "Without", "Able", "Feel", "Feels", "Felt", "Does", "Did",
    "Done", "Didn", "Doesn", "Wasn", "Weren", "Isn", "Aren", "Won", "Don", "Couldn", "Wouldn", "Shouldn",
    "Haven", "Hasn", "Hadn",
}


def classify_conversational_act(text: str, speaker: Optional[str] = None) -> str:
    """Deterministic rule-based conversational act classifier. Returns one of CANONICAL_ACTS."""
    cleaned = (text or "").strip()
    if not cleaned:
        return "status_update"

    low = cleaned.lower()

    # 1. Topic Shift: explicit markers of agenda transition
    topic_shift_cues = (
        "moving on to", "shift gears", "shifting gears", "on another note", "next item on the agenda",
        "next item", "let's turn to", "turn to the", "changing subjects", "next topic", "let's move to", "switch gears",
    )
    if any(cue in low for cue in topic_shift_cues):
        return "topic_shift"

    # 2. Decision Point: explicit resolution, consensus, or selection
    decision_cues = (
        "we decided", "we have decided", "the decision is", "let's go with", "we agreed on",
        "agreed on", "we are selecting", "we will proceed with", "lock it in", "final decision",
        "it's decided", "resolved to",
    )
    if any(cue in low for cue in decision_cues):
        return "decision_point"

    # 3. Objection / Risk / Concern: explicit resistance, risk, or blocker
    objection_cues = (
        "i disagree", "we can't", "can't commit", "cannot commit", "my main concern",
        "main concern is", "severe risk", "high risk", "risk of", "hesitant to",
        "the blocker", "blocker right now", "push back", "problem with that",
        "not feasible", "drawback", "not sure that works",
    )
    if any(cue in low for cue in objection_cues):
        return "objection"

    # 4. Commitment: action item, promise, deliverable ownership
    commitment_cues = (
        "i will draft", "i will deliver", "we will deliver", "we will follow up",
        "we will complete", "i'll take the action", "take the action item", "i can send",
        "committed to completing", "committed to", "i will own", "my deliverable",
        "assigned to", "action item for",
    )
    if any(cue in low for cue in commitment_cues):
        return "commitment"

    # 5. Question to Operator / Group: direct interrogative
    question_starters = (
        "can you", "could you", "what do you think", "is there any", "is there",
        "how should we", "how do we", "what is", "what are", "do we have",
        "should we", "would you", "who will", "where do", "why do",
    )
    if cleaned.endswith("?") or any(low.startswith(qs) for qs in question_starters) or ("?" in cleaned and any(qs in low for qs in question_starters)):
        return "question_to_operator"
    if low.startswith(("i will ", "we will ", "i'll ")):
        return "commitment"
    return "status_update"


_CONTRACTION_STRIP_RE = re.compile(
    r"\b(?:[a-zA-Z]+n['’]t|[a-zA-Z]+['’](?:re|ve|ll|m|d)|(?:it|that|what|there|here|who|where|when|why|how|he|she|let)['’]s)\b",
    re.IGNORECASE,
)


def _extract_proper_noun_entities(text: str, entities: Set[str]) -> None:
    clean_text = _CONTRACTION_STRIP_RE.sub(" ", text)
    for match in PROPER_NOUN_PATTERN.finditer(clean_text):
        words = [w for w in match.group(0).strip().split() if not w.isdigit()]
        if len(words) == 1 and words[0] not in STOPWORDS and len(words[0]) >= 3:
            entities.add(words[0])
        elif len(words) > 1:
            filtered = [w for i, w in enumerate(words) if w not in STOPWORDS or (i > 0 and w == "More" and words[i - 1] not in STOPWORDS)]
            if filtered:
                entities.add(" ".join(filtered))


_DOMAIN_PHRASE_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bsow\s*1\b", re.IGNORECASE), "SOW 1"),
    (re.compile(r"\bsow\s*2\b", re.IGNORECASE), "SOW 2"),
    (re.compile(r"\bgolden\s+datasets?\b", re.IGNORECASE), "Golden Dataset"),
    (re.compile(r"\bserial\s+numbers?\b", re.IGNORECASE), "Serial Number"),
    (re.compile(r"\bsingle\s+turbine\b", re.IGNORECASE), "Single Turbine"),
    (re.compile(r"\bturbine\s+fleet\b", re.IGNORECASE), "Turbine Fleet"),
)


def _extract_pattern_tokens(text: str) -> Set[str]:
    tokens: Set[str] = {m.group(0).strip() for m in ENTITY_ID_PATTERN.finditer(text)}
    tokens.update(m.group(0).strip() for m in ACRONYM_PATTERN.finditer(text) if m.group(0).strip() not in STOPWORDS and len(m.group(0).strip()) >= 2)
    for pat, canonical in _DOMAIN_PHRASE_PATTERNS:
        if pat.search(text):
            tokens.add(canonical)
    return tokens


LOWER_STOPWORDS: Set[str] = {w.lower() for w in STOPWORDS} | {
    "about", "above", "after", "again", "against", "already", "always", "around", "because", "been", "before", "being", "below", "between", "book", "bookies", "both", "cannot", "check", "comment", "comments", "could", "doing", "down", "during", "each", "either", "enough", "every", "further", "have", "having", "here", "itself", "just", "know", "like", "make", "many", "might", "more", "most", "much", "must", "need", "needs", "never", "neither", "once", "only", "other", "ought", "ours", "quite", "rather", "react", "really", "right", "said", "same", "seen", "several", "shall", "should", "since", "some", "still", "stuff", "such", "sure", "take", "tell", "than", "that", "their", "theirs", "them", "themselves", "then", "there", "these", "they", "thing", "things", "think", "this", "those", "through", "time", "today", "together", "under", "until", "upon", "very", "view", "want", "well", "were", "what", "whatever", "when", "where", "whether", "which", "while", "whom", "whose", "will", "with", "within", "without", "would", "yesterday", "your", "able", "feel", "feels", "felt", "does", "didn", "doesn", "wasn", "weren", "couldn", "wouldn", "shouldn", "haven", "hasn", "hadn", "going", "went", "gone", "come", "came", "coming", "give", "gave", "given", "getting", "gotten", "find", "found", "keep", "kept", "made", "making", "makes", "mean", "means", "meant", "putting", "seem", "seems", "seemed", "show", "shows", "showed", "start", "started", "starting", "talk", "talked", "talking", "telling", "told", "turned", "turning", "used", "using", "uses", "work", "worked", "working", "works", "help", "helped", "helping", "helps", "asked", "asking", "asks", "tried", "trying", "tries", "called", "calling", "calls", "sometimes",
}


def _extract_lowercase_domain_terms(text: str) -> Set[str]:
    clean_low = _CONTRACTION_STRIP_RE.sub(" ", text.lower())
    candidates = [w for w in re.findall(r"\b[a-z][a-z0-9_\-]{3,}\b", clean_low) if w not in LOWER_STOPWORDS and not w.isdigit()]
    return set(dict.fromkeys(candidates[:12]))


def extract_turn_entities(text: str) -> List[str]:
    """Extracts candidate entities from turn text without provider calls."""
    if not text or has_c19_marker(text):
        return []
    entities = _extract_pattern_tokens(text)
    _extract_proper_noun_entities(text, entities)
    if not entities:
        entities.update(_extract_lowercase_domain_terms(text))
    return sorted(e for e in entities if not has_c19_marker(e) and len(e) >= 2)


@dataclass
class TurnRecord:
    """Internal representation of an ingested meeting turn."""
    seq: int
    speaker: str
    text: str
    timestamp: str
    turn_sec: float
    act: str
    entities: List[str]
    is_quarantined: bool = False
    asr_conf: float = 1.0
    speaker_conf: float = 1.0
    addressee: Optional[str] = None
    ask_kind: Optional[str] = None
    is_backchannel: bool = False
    is_sibling_session: bool = False


@dataclass
class EntitySalienceRecord:
    """Tracking structure for exponentially decayed entity salience."""
    entity: str
    accumulated_ewma: float = 0.0
    last_seen_sec: float = 0.0
    mention_count: int = 0
    first_seen_sec: float = 0.0


@dataclass
class SpeakerNoteProfile:
    """Per-speaker longitudinal note profile tracking stances, commitments, objections, and entities."""
    speaker: str
    turn_count: int = 0
    last_seen_sec: float = 0.0
    stances: List[str] = field(default_factory=list)
    commitments: List[str] = field(default_factory=list)
    objections_and_risks: List[str] = field(default_factory=list)
    open_asks: List[str] = field(default_factory=list)
    salient_entities: List[str] = field(default_factory=list)


@dataclass
class FlowState:
    """Structured running state of meeting dynamics."""
    topic_segments: List[Dict[str, Any]] = field(default_factory=list)
    open_questions: List[Dict[str, Any]] = field(default_factory=list)
    commitments: List[Dict[str, Any]] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    objections_and_risks: List[Dict[str, Any]] = field(default_factory=list)
    speaker_turns: Dict[str, int] = field(default_factory=dict)
    speaker_last_seen: Dict[str, float] = field(default_factory=dict)
    dominant_speaker: Optional[str] = None
    quiet_speakers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic_segments": list(self.topic_segments),
            "open_questions": list(self.open_questions),
            "commitments": list(self.commitments),
            "decisions": list(self.decisions),
            "objections_and_risks": list(self.objections_and_risks),
            "speaker_turns": dict(self.speaker_turns),
            "dominant_speaker": self.dominant_speaker,
            "quiet_speakers": list(self.quiet_speakers),
        }


class ConversationContextModel:
    """
    Deterministic, provider-free two-layer context model for Live Copilot.
    Updated incrementally on every turn in O(1) amortized time.
    """

    def __init__(
        self,
        project_scope: Optional[str] = None,
        attendees: Optional[List[Dict[str, str]]] = None,
        focus_seconds: Optional[float] = None,
        decay_tau_seconds: Optional[float] = None,
        salient_entities_top_k: Optional[int] = None,
        flow_items_max: Optional[int] = None,
        focus_weight: Optional[float] = None,
    ) -> None:
        self.project_scope = project_scope or "UNRESOLVED"
        self.attendees = attendees or []
        self._explicit_focus_seconds = focus_seconds is not None
        self.focus_seconds = float(focus_seconds if focus_seconds is not None else get_copilot_context_focus_seconds())
        self.decay_tau = float(decay_tau_seconds if decay_tau_seconds is not None else get_copilot_context_decay_tau_seconds())
        self.salient_entities_top_k = salient_entities_top_k if salient_entities_top_k is not None else get_copilot_context_salient_entities_top_k()
        self.flow_items_max = flow_items_max if flow_items_max is not None else get_copilot_context_flow_items_max()
        self.focus_weight = float(focus_weight if focus_weight is not None else get_copilot_context_focus_weight())

        self._turns: List[TurnRecord] = []
        self._turn_counter = 0
        self._latest_time: float = 0.0
        self._entity_salience: Dict[str, EntitySalienceRecord] = {}
        self._speaker_profiles: Dict[str, SpeakerNoteProfile] = {}
        self._flow_state = FlowState()
        self._current_act: str = "status_update"

        self._known_attendees: Set[str] = set()
        for a in self.attendees:
            if isinstance(a, dict):
                name = (a.get("name") or a.get("canonical_name") or a.get("display_name") or "").strip()
                if name:
                    self._known_attendees.add(name)
            elif isinstance(a, str) and a.strip():
                self._known_attendees.add(a.strip())
        self.episode_tracker = EpisodeTracker()
        from .copilot_turn_signals import AskLedger
        self._ask_ledger = AskLedger()

    @property
    def ask_ledger(self) -> Any:
        """Returns the conversation ask ledger tracking open and answered questions."""
        return self._ask_ledger

    @property
    def current_act(self) -> str:
        """Returns the classified conversational act of the latest dialogue moment."""
        return self._current_act

    @property
    def flow_state(self) -> FlowState:
        """Returns the current structured flow state."""
        return self._flow_state

    def resolve_question(
        self, episode_id: Optional[str] = None, resolution_text: str = "", turn_seq: Optional[int] = None
    ) -> bool:
        """Marks a question episode resolved so it stops recurring in advice."""
        seq = turn_seq or self._turn_counter
        if episode_id:
            res = self.episode_tracker.resolve_question(episode_id, resolution_text, seq)
            for q in self._flow_state.open_questions:
                if q.get("episode_id") == episode_id:
                    q["resolved"] = True
                    q["resolution_text"] = resolution_text
            return res
        for q in reversed(self._flow_state.open_questions):
            if not q.get("resolved"):
                q["resolved"] = True
                q["resolution_text"] = resolution_text
                if q.get("episode_id"):
                    self.episode_tracker.resolve_question(q["episode_id"], resolution_text, seq)
                return True
        return False

    def _handle_quarantined_turn(
        self, seq: int, timestamp: str, sec: float, asr_conf: float, speaker_conf: float
    ) -> int:
        self._turns.append(TurnRecord(
            seq=seq, speaker="[QUARANTINED]", text="[REDACTED_BY_C19_FIREWALL]",
            timestamp=timestamp, turn_sec=sec, act="status_update", entities=[],
            is_quarantined=True, asr_conf=asr_conf, speaker_conf=speaker_conf,
            addressee=None, ask_kind=None, is_backchannel=False,
        ))
        return seq

    def _process_ask_ledger_signals(
        self, seq: int, speaker: str, text: str, sec: float,
        is_ask: bool, bc_flag: bool, eff_ask_kind: Optional[str],
        resolved_addressee: Optional[str],
    ) -> None:
        if is_ask and not bc_flag:
            self._ask_ledger.register_ask(
                turn_seq=seq, speaker=speaker, text=text,
                addressee=resolved_addressee, ask_kind=eff_ask_kind or "open", created_sec=sec,
            )
        answered = self._ask_ledger.process_turn(turn_seq=seq, speaker=speaker, text=text, is_bc=bc_flag)
        for ans in answered:
            self.resolve_question(resolution_text=ans.answer_text or "", turn_seq=seq)

    def _update_turn_entities_salience(self, entities: List[str], sec: float) -> None:
        for ent in entities:
            clean_ent = ent.strip()
            if not clean_ent:
                continue
            if clean_ent not in self._entity_salience:
                self._entity_salience[clean_ent] = EntitySalienceRecord(
                    entity=clean_ent, accumulated_ewma=1.0, last_seen_sec=sec,
                    mention_count=1, first_seen_sec=sec,
                )
            else:
                rec = self._entity_salience[clean_ent]
                dt = max(0.0, sec - rec.last_seen_sec)
                decay_factor = math.exp(-dt / self.decay_tau) if self.decay_tau > 0 else 0.0
                rec.accumulated_ewma = rec.accumulated_ewma * decay_factor + 1.0
                rec.last_seen_sec = sec
                rec.mention_count += 1

    def add_turn(
        self,
        speaker: str,
        text: str,
        timestamp: str,
        turn_sec: Optional[float] = None,
        asr_conf: float = 1.0,
        speaker_conf: float = 1.0,
        addressee: Optional[str] = None,
        ask_kind: Optional[str] = None,
        is_backchannel: Optional[bool] = None,
        is_sibling_session: bool = False,
    ) -> int:
        """Incrementally ingests a confirmed dialogue turn in O(1) amortized time."""
        from dual_pipeline.audio_postprocess import is_hallucinated_or_prompt_echo_turn
        self._turn_counter += 1
        seq = self._turn_counter
        sec = float(turn_sec) if turn_sec is not None else float(self._turn_counter * 3.0)
        if is_hallucinated_or_prompt_echo_turn(text) or has_c19_marker(text) or has_c19_marker(speaker):
            return self._handle_quarantined_turn(seq, timestamp, sec, asr_conf, speaker_conf)
        self._latest_time = max(self._latest_time, sec)
        from .copilot_turn_signals import detect_ask_signals, detect_addressee
        is_ask, inferred_kind, detected_bc = detect_ask_signals(text)
        bc_flag = detected_bc if is_backchannel is None else bool(is_backchannel)
        eff_ask_kind = ask_kind or inferred_kind
        cand_roster = [{"name": n, "canonical_name": n} for n in self._known_attendees]
        resolved_addressee = addressee or detect_addressee(text, cand_roster)
        act = classify_conversational_act(text, speaker)
        if is_ask and act == "status_update":
            act = "question_to_operator"
        self._current_act = act
        entities = extract_turn_entities(text)
        turn_record = TurnRecord(
            seq=seq, speaker=speaker, text=text, timestamp=timestamp, turn_sec=sec,
            act=act, entities=entities, is_quarantined=False, asr_conf=asr_conf,
            speaker_conf=speaker_conf, addressee=resolved_addressee,
            ask_kind=eff_ask_kind, is_backchannel=bc_flag, is_sibling_session=is_sibling_session,
        )
        self._turns.append(turn_record)
        self._process_ask_ledger_signals(
            seq, speaker, text, sec, is_ask, bc_flag, eff_ask_kind, resolved_addressee
        )
        self._update_turn_entities_salience(entities, sec)
        self._update_flow_state(turn_record)
        return seq

    def _append_flow_item(self, target_list: List[Dict[str, Any]], item: Dict[str, Any]) -> None:
        target_list.append(item)
        if len(target_list) > self.flow_items_max:
            target_list.pop(0)

    def _update_speaker_stats(self, turn: TurnRecord) -> None:
        fs = self._flow_state
        spk = turn.speaker
        fs.speaker_turns[spk] = fs.speaker_turns.get(spk, 0) + 1
        fs.speaker_last_seen[spk] = turn.turn_sec

        total_turns = sum(fs.speaker_turns.values())
        if total_turns >= 10:
            for speaker_name, count in fs.speaker_turns.items():
                if (count / total_turns) > 0.85:
                    fs.dominant_speaker = speaker_name
                    break
            else:
                fs.dominant_speaker = None
        else:
            fs.dominant_speaker = None

        if total_turns >= 10 and self._known_attendees:
            quiet = []
            for att_name in sorted(self._known_attendees):
                last_seen = fs.speaker_last_seen.get(att_name)
                if last_seen is None or (turn.turn_sec - last_seen > 120.0):
                    quiet.append(att_name)
            fs.quiet_speakers = quiet

    def _update_speaker_profile(self, turn: TurnRecord) -> None:
        key = turn.speaker.strip().lower()
        if not key or turn.is_quarantined:
            return
        prof = self._speaker_profiles.get(key)
        if prof is None:
            prof = SpeakerNoteProfile(speaker=turn.speaker.strip())
            self._speaker_profiles[key] = prof
        prof.turn_count += 1
        prof.last_seen_sec = turn.turn_sec
        for e in turn.entities:
            if e not in prof.salient_entities:
                prof.salient_entities.append(e)
        txt = turn.text.strip()
        if not txt or turn.is_backchannel:
            return
        if turn.act == "commitment":
            prof.commitments = (prof.commitments + [txt])[-4:]
        elif turn.act == "objection":
            prof.objections_and_risks = (prof.objections_and_risks + [txt])[-4:]
        elif turn.act == "question_to_operator":
            prof.open_asks = (prof.open_asks + [txt])[-4:]
        else:
            prof.stances = (prof.stances + [txt])[-5:]

    def get_speaker_notes(self, speaker: str) -> Optional[SpeakerNoteProfile]:
        """Returns the longitudinal SpeakerNoteProfile for a speaker (case-insensitive)."""
        return self._speaker_profiles.get((speaker or "").strip().lower())

    def _update_flow_state(self, turn: TurnRecord) -> None:
        """Incrementally updates flow state components."""
        self._update_speaker_stats(turn)
        self._update_speaker_profile(turn)
        fs = self._flow_state

        base_item = {
            "seq": turn.seq,
            "speaker": turn.speaker,
            "text": turn.text,
            "timestamp": turn.timestamp,
            "turn_sec": turn.turn_sec,
        }

        if turn.act == "topic_shift":
            self._append_flow_item(fs.topic_segments, {**base_item, "key_entities": turn.entities})
        elif turn.act == "question_to_operator":
            ep = self.episode_tracker.add_question(turn.speaker, turn.text, turn.seq, turn.turn_sec)
            self._append_flow_item(fs.open_questions, {**base_item, "resolved": False, "episode_id": ep.episode_id})
        elif turn.act == "commitment":
            self._append_flow_item(fs.commitments, base_item)
        elif turn.act == "decision_point":
            self._append_flow_item(fs.decisions, base_item)
        elif turn.act == "objection":
            self._append_flow_item(fs.objections_and_risks, {**base_item, "resolved": False})

        reopened = self.episode_tracker.evaluate_contradiction(turn.text, turn.seq)
        if reopened:
            reopened_ids = {ep.episode_id for ep in reopened}
            for q in fs.open_questions:
                if q.get("episode_id") in reopened_ids:
                    q["resolved"] = False

    @staticmethod
    def _turn_to_dict(t: TurnRecord) -> Dict[str, Any]:
        return {
            "seq": t.seq, "speaker": t.speaker, "text": t.text, "timestamp": t.timestamp,
            "turn_sec": t.turn_sec, "act": t.act, "entities": list(t.entities),
            "asr_conf": t.asr_conf, "speaker_conf": t.speaker_conf,
            "addressee": t.addressee, "ask_kind": t.ask_kind, "is_backchannel": t.is_backchannel, "is_sibling_session": bool(t.is_sibling_session),
        }

    def focus_turns(self) -> List[Dict[str, Any]]:
        """Returns turns within focus_seconds window at full weight (1.0), non-quarantined only."""
        cutoff = self._latest_time - self.focus_seconds
        non_q = [t for t in self._turns if not t.is_quarantined]
        selected = [t for t in non_q if t.turn_sec >= cutoff]
        if not getattr(self, "_explicit_focus_seconds", False) and len(selected) < 25 and len(non_q) > len(selected):
            selected = non_q[-25:]
            sibs = [t for t in non_q if t.is_sibling_session and t not in selected]
            if sibs:
                selected = sibs[-3:] + selected
        elif not selected and non_q:
            selected = non_q[-10:]
        return [self._turn_to_dict(t) for t in selected]

    def salient_entities(self, top_k: Optional[int] = None) -> List[Tuple[str, float]]:
        """Returns top ranked entities combining focus layer weight and decayed whole-meeting salience."""
        k = top_k if top_k is not None else self.salient_entities_top_k
        if not self._entity_salience:
            return []
        focus_counts: Dict[str, int] = {}
        for ft in self.focus_turns():
            ents = ft.get("entities")
            for ent in (ents if isinstance(ents, list) else extract_turn_entities(ft.get("text", ""))):
                focus_counts[ent] = focus_counts.get(ent, 0) + 1
        scores: List[Tuple[str, float]] = []
        current_time = self._latest_time
        for ent, rec in self._entity_salience.items():
            if has_c19_marker(ent):
                continue
            dt = max(0.0, current_time - rec.last_seen_sec)
            decayed = rec.accumulated_ewma * math.exp(-dt / self.decay_tau) if self.decay_tau > 0 else 0.0
            combined = (self.focus_weight * float(focus_counts.get(ent, 0))) + ((1.0 - self.focus_weight) * decayed)
            scores.append((ent, combined))
        scores.sort(key=lambda item: (-round(item[1], 6), item[0]))
        return scores[:k]

    @staticmethod
    def _add_unquarantined(items: Iterable[str], seeds: Set[str]) -> None:
        for item in items:
            if not has_c19_marker(item):
                seeds.add(item)

    def _collect_flow_state_seeds(self, seeds: Set[str]) -> None:
        fs = self._flow_state
        if fs.topic_segments:
            self._add_unquarantined(fs.topic_segments[-1].get("key_entities", []), seeds)
        if fs.open_questions:
            self._add_unquarantined(extract_turn_entities(fs.open_questions[-1].get("text", "")), seeds)
        if fs.objections_and_risks:
            self._add_unquarantined(extract_turn_entities(fs.objections_and_risks[-1].get("text", "")), seeds)

    def retrieval_seeds(self) -> List[str]:
        """
        Synthesizes a high-salience, deduplicated list of retrieval seeds for GraphDB lookup:
          - Top salient entities (focus + decayed, suppressing uncorroborated single-occurrence terms)
          - Flow state anchors (active topic entities, open question terms, objection terms)
          - Attendee names and project scope
        """
        focus_ents = {e for ft in self.focus_turns() for e in (ft.get("entities") if isinstance(ft.get("entities"), list) else extract_turn_entities(ft.get("text", "")))}
        seeds: Set[str] = {
            ent for ent, _ in self.salient_entities()
            if not has_c19_marker(ent) and not (
                (rec := self._entity_salience.get(ent)) and rec.mention_count <= 1 and ent not in focus_ents
            )
        }

        self._collect_flow_state_seeds(seeds)
        if self.project_scope and self.project_scope != "UNRESOLVED":
            seeds.add(self.project_scope)
        return sorted(s for s in seeds if not has_c19_marker(s))

    def compute_hash(self) -> str:
        """Computes deterministic RFC-8259 SHA-256 digest of the context model state."""
        salient_serialized = [[ent, round(score, 4)] for ent, score in self.salient_entities()]
        state_payload = {
            "project_scope": self.project_scope, "turn_count": len([t for t in self._turns if not t.is_quarantined]),
            "latest_time": round(self._latest_time, 3), "current_act": self._current_act,
            "salient_entities": salient_serialized, "retrieval_seeds": self.retrieval_seeds(),
            "dominant_speaker": self._flow_state.dominant_speaker, "quiet_speakers": self._flow_state.quiet_speakers,
        }
        canonical = json.dumps(state_payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def format_flow_summary(self) -> str:
        """Formats concise flow state summary block for LLM prompt context."""
        parts = []
        fs = self._flow_state
        if fs.topic_segments:
            parts.append(f"Current Topic: {fs.topic_segments[-1].get('text', '')}")
        if fs.open_questions:
            unresolved = [q for q in fs.open_questions if not q.get("resolved")]
            if unresolved:
                parts.append(f"Open Question: {unresolved[-1].get('speaker')}: {unresolved[-1].get('text')}")
        if fs.decisions:
            parts.append(f"Recent Decision: {fs.decisions[-1].get('text')}")
        if fs.commitments:
            parts.append(f"Recent Commitment: {fs.commitments[-1].get('speaker')}: {fs.commitments[-1].get('text')}")
        if fs.objections_and_risks:
            parts.append(f"Active Concern: {fs.objections_and_risks[-1].get('text')}")
        if fs.dominant_speaker:
            parts.append(f"Speaker Dynamics: Monologue detected ({fs.dominant_speaker} driving >85% of turns)")
        return " | ".join(parts) if parts else "Flow: Steady discussion"
