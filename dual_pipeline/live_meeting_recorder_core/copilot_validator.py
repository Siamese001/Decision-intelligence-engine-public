"""
dual_pipeline/live_meeting_recorder_core/copilot_validator.py

Post-synthesis validator and anti-hallucination guard for Live Executive Copilot.
Enforces:
- Rule C-19: Career leverage & internal advisory firewall.
- Rule C-20: Anti-hallucination invariant (unauthorized commercial promises,
  free commitment traps, and ownership/DRI ground truth verification against GraphDB).
- Word limit constraints and structural schema integrity.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .copilot_retriever import has_c19_marker

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_validator")

# Response Length Budget Constants governed by parameters.json SSOT
from dual_pipeline.parameters import get_copilot_speech_limits

_c_limits = get_copilot_speech_limits()
MAX_SPEAKER_SUMMARY_WORDS = _c_limits["speaker_summary_max_words"]
MAX_SUPPORTING_DETAILS_WORDS = _c_limits["supporting_details_max_words"]
MAX_TOTAL_CONTENT_WORDS = _c_limits["total_content_max_words"]


def _truncate_at_sentence_boundary(text: str, max_words: int) -> str:
    """Truncates text at sentence boundaries when possible before falling back to word cap."""
    words = text.split()
    if len(words) <= max_words:
        return text
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if len(sentences) > 1:
        kept: List[str] = []
        used = 0
        for s in sentences:
            sw = len(s.split())
            if used + sw <= max_words:
                kept.append(s)
                used += sw
            else:
                break
        if kept:
            return " ".join(kept)
    return " ".join(words[:max_words]).rstrip(",;:- ")


def _count_words(text: str) -> int:
    """Counts whitespace-delimited words in text."""
    if not text:
        return 0
    return len(str(text).split())


# Rule C-20: Unauthorized Commercial & Delivery Commitment Patterns
UNAUTHORIZED_COMMITMENT_PATTERNS = (
    re.compile(
        r"\b(?:for\s+free|at\s+no\s+cost|zero\s+cost|at\s+no\s+charge|free\s+of\s+charge|complimentary|on\s+the\s+house|pro\s+bono)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:without\s+(?:extra|additional)\s+(?:cost|budget|charge|funding))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:deliver|finish|ship|complete)\s+(?:in\s+(?:1|2|one|two)\s+days?|by\s+tomorrow|tomorrow)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:unlimited\s+(?:revisions?|changes?|updates?|work))\b",
        re.IGNORECASE,
    ),
)

# Standard executive Agree-and-Bound fallbacks for ungrounded/unauthorized commitments
AGREE_AND_BOUND_FALLBACK_SCRIPT = (
    "I suggest we bound this to an initial two-week spike to establish verified scope before committing."
)
AGREE_AND_BOUND_FALLBACK_SNAP = "Bound scope to spike"
AGREE_AND_BOUND_RISK = "Unbudgeted commitment"

VERIFY_OWNERSHIP_FALLBACK_SCRIPT = (
    "Can we verify technical delivery ownership and milestones before confirming scope?"
)
VERIFY_OWNERSHIP_FALLBACK_SNAP = "Verify milestone ownership"

# Energy Transmission & Utility Domain Integrity Patterns (APEX_TRANS Discussion Grounding)
DIRECT_SCADA_INGRESS_PATTERN = re.compile(
    r"\b(?:direct\s+(?:scada|ems)\s+(?:access|ingress|connection|integration|read|write)|connect\s+directly\s+to\s+scada)\b",
    re.IGNORECASE,
)
TIMELINE_2027_PUSHBACK_PATTERN = re.compile(
    r"\b(?:wait\s+(?:until|for)\s+2027|delay\s+(?:until|to)\s+2027|defer\s+(?:all|everything)\s+to\s+2027|commit\s+(?:full\s+scope\s+to|to\s+deliver\s+in)\s+2027)\b",
    re.IGNORECASE,
)
CUSTOM_VISION_BUILD_PATTERN = re.compile(
    r"\b(?:build\s+(?:our\s+own|custom)\s+(?:camera|computer\s+vision|wildfire|detection)|rebuild\s+pano)\b",
    re.IGNORECASE,
)

SCADA_DECOUPLING_SCRIPT = (
    "We decouple real-time SCADA via PI Historian replication, ensuring zero operational risk to EMS."
)
SCADA_DECOUPLING_SNAP = "Decouple SCADA via PI"
SCADA_DECOUPLING_RISK = "NERC-CIP CIP-005 violation"

AGREE_AND_BOUND_2027_SCRIPT = (
    "We agree on the 2027 target, but prove the high-value edge cases in a 30-day spike first."
)
AGREE_AND_BOUND_2027_SNAP = "Anchor 2027 to 30-day spike"
AGREE_AND_BOUND_2027_RISK = "Premature 2027 commitment"

PANO_AI_REVERSE_PROBING_SCRIPT = (
    "Do we want to take on maintenance of custom computer vision when Pano AI already integrates with our cameras?"
)
PANO_AI_REVERSE_PROBING_SNAP = "Leverage Pano AI SaaS"
PANO_AI_TOOL_FATIGUE_RISK = "Custom build tool fatigue"


def validate_anti_hallucination_commitments(
    script: str,
    snap: str,
) -> bool:
    """Returns True if unauthorized commercial or delivery commitments are detected."""
    combined = f"{script} {snap}"
    return any(p.search(combined) for p in UNAUTHORIZED_COMMITMENT_PATTERNS)


def _record_assertion_owner(
    assertion: Dict[str, Any],
    nodes: Dict[str, Any],
    owner_map: Dict[str, Set[str]],
) -> None:
    """Records normalized subject and canonical name as valid owners for the assertion object."""
    predicate = assertion.get("predicate", "")
    if predicate not in {"OWNS", "LEADS", "OWNS_OR_LEADS", "DELIVERS"}:
        return
    subj = assertion.get("subject_node_id") or assertion.get("subject_node") or ""
    obj = assertion.get("object_node_id") or assertion.get("object_literal") or ""
    if not (subj and obj):
        return
    owner_map.setdefault(obj.lower(), set()).add(subj.lower())
    if subj in nodes:
        c_name = nodes[subj].get("canonical_name", "").lower()
        if c_name:
            owner_map.setdefault(obj.lower(), set()).add(c_name)


def extract_verified_owners(topology: Any) -> Dict[str, Set[str]]:
    """Extracts mapping of target entity/system canonical names or IDs to set of verified owner names/IDs."""
    cached = getattr(topology, "_cached_owner_map", None)
    if isinstance(cached, dict):
        return cached
    if topology is None:
        return {}
    owner_map: Dict[str, Set[str]] = {}
    assertions = getattr(topology, "assertions", {})
    items = assertions.values() if isinstance(assertions, dict) else assertions
    nodes = getattr(topology, "nodes", {}) or {}
    for a in items:
        if isinstance(a, dict):
            _record_assertion_owner(a, nodes, owner_map)
    topology._cached_owner_map = owner_map
    return owner_map


def _scan_for_c19(data: Any) -> bool:
    if isinstance(data, str):
        return has_c19_marker(data)
    elif isinstance(data, dict):
        return any(_scan_for_c19(v) for v in data.values())
    elif isinstance(data, list):
        return any(_scan_for_c19(v) for v in data)
    return False


def _scan_for_secrets(data: Any) -> bool:
    from dual_pipeline.sanitizer import SECRET_PATTERNS
    if isinstance(data, str):
        return any(p.search(data) for p in SECRET_PATTERNS)
    elif isinstance(data, dict):
        return any(_scan_for_secrets(v) for v in data.values())
    elif isinstance(data, list):
        return any(_scan_for_secrets(v) for v in data)
    return False


def _scrub_c19_dict_entry(k: str, v: Any) -> Tuple[bool, Any]:
    """Helper evaluating a single dict entry for C-19 scrubbing."""
    if has_c19_marker(str(k)):
        return False, None
    if not has_c19_marker(v):
        return True, _deep_scrub_c19(v)
    if k == "suggested_script":
        return True, VERIFY_OWNERSHIP_FALLBACK_SCRIPT
    if k == "snap_cue":
        return True, VERIFY_OWNERSHIP_FALLBACK_SNAP
    if isinstance(v, (list, dict)):
        return True, _deep_scrub_c19(v)
    return True, VERIFY_OWNERSHIP_FALLBACK_SNAP


def _deep_scrub_c19(data: Any) -> Any:
    if isinstance(data, str):
        return VERIFY_OWNERSHIP_FALLBACK_SNAP if has_c19_marker(data) else data
    if isinstance(data, list):
        return [_deep_scrub_c19(item) for item in data if not has_c19_marker(item)]
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            keep, scrubbed_val = _scrub_c19_dict_entry(k, v)
            if keep:
                cleaned[k] = scrubbed_val
        return cleaned
    return data


def _check_c19_career_firewall(
    parsed: Dict[str, Any],
    script: str,
    snap: str,
) -> Tuple[str, str]:
    """Applies Rule C-19 Career Leverage & Internal Advisory Firewall."""
    if _scan_for_c19(parsed) or has_c19_marker(script) or has_c19_marker(snap):
        logger.warning("C-19 marker detected in copilot script; applying executive sanitization fallback")
        scrubbed = _deep_scrub_c19(parsed)
        parsed.clear()
        parsed.update(scrubbed)
        parsed["suggested_script"] = VERIFY_OWNERSHIP_FALLBACK_SCRIPT
        parsed["snap_cue"] = VERIFY_OWNERSHIP_FALLBACK_SNAP
        if isinstance(scrubbed.get("speaker_summary_quotes"), list):
            parsed["speaker_summary_quotes"] = scrubbed.get("speaker_summary_quotes", [])
            if not parsed["speaker_summary_quotes"]:
                parsed["speaker_summary_quotes"] = [f'"{VERIFY_OWNERSHIP_FALLBACK_SCRIPT}"']
        else:
            parsed["speaker_summary_quotes"] = f'"{VERIFY_OWNERSHIP_FALLBACK_SCRIPT}"'
        if not parsed.get("supporting_details_bullets"):
            parsed["supporting_details_bullets"] = [VERIFY_OWNERSHIP_FALLBACK_SNAP]
        return parsed["suggested_script"], parsed["snap_cue"]
    return script, snap


def _check_c20_commitments(
    parsed: Dict[str, Any],
    script: str,
    snap: str,
) -> Tuple[str, str]:
    """Applies Rule C-20 Anti-Hallucination for unauthorized commercial/delivery commitments."""
    if validate_anti_hallucination_commitments(script, snap):
        logger.warning("C-20 unauthorized commitment detected in copilot script; applying Agree-and-Bound fallback")
        parsed["suggested_script"] = AGREE_AND_BOUND_FALLBACK_SCRIPT
        parsed["snap_cue"] = AGREE_AND_BOUND_FALLBACK_SNAP
        parsed["category"] = "COMMERCIAL"
        parsed["risk_trap"] = AGREE_AND_BOUND_RISK
        parsed["risk_alert"] = AGREE_AND_BOUND_RISK
        return parsed["suggested_script"], parsed["snap_cue"]
    return script, snap


def _check_energy_domain_integrity(
    parsed: Dict[str, Any],
    script: str,
    snap: str,
) -> Tuple[str, str]:
    """Guards energy/utility domain constraints: NERC-CIP perimeter, 2027 timeline, and SaaS leverage."""
    combined = f"{script} {snap}"

    # 1. SCADA / NERC-CIP CIP-005 perimeter defense: direct access forbidden without PI Historian decoupling
    if DIRECT_SCADA_INGRESS_PATTERN.search(combined) and "pi historian" not in script.lower():
        logger.warning("Direct SCADA ingress proposal intercepted; applying PI Historian decoupling fallback")
        parsed["suggested_script"] = SCADA_DECOUPLING_SCRIPT
        parsed["snap_cue"] = SCADA_DECOUPLING_SNAP
        parsed["category"] = "TECHNICAL"
        parsed["risk_trap"] = SCADA_DECOUPLING_RISK
        parsed["risk_alert"] = SCADA_DECOUPLING_RISK
        return parsed["suggested_script"], parsed["snap_cue"]

    # 2. Timeline 2027 pushback: anchor to 30-day spike
    if TIMELINE_2027_PUSHBACK_PATTERN.search(combined):
        logger.warning("Unbounded 2027 commitment/pushback intercepted; applying 30-day spike fallback")
        parsed["suggested_script"] = AGREE_AND_BOUND_2027_SCRIPT
        parsed["snap_cue"] = AGREE_AND_BOUND_2027_SNAP
        parsed["category"] = "STRATEGIC"
        parsed["risk_trap"] = AGREE_AND_BOUND_2027_RISK
        parsed["risk_alert"] = AGREE_AND_BOUND_2027_RISK
        return parsed["suggested_script"], parsed["snap_cue"]

    # 3. Custom computer vision build vs Pano AI SaaS
    if CUSTOM_VISION_BUILD_PATTERN.search(combined):
        logger.warning("Custom computer vision build proposal intercepted; applying Pano AI SaaS reverse-probe")
        parsed["suggested_script"] = PANO_AI_REVERSE_PROBING_SCRIPT
        parsed["snap_cue"] = PANO_AI_REVERSE_PROBING_SNAP
        parsed["category"] = "TECHNICAL"
        parsed["risk_trap"] = PANO_AI_TOOL_FATIGUE_RISK
        parsed["risk_alert"] = PANO_AI_TOOL_FATIGUE_RISK
        return parsed["suggested_script"], parsed["snap_cue"]

    return script, snap


def _check_c20_graph_ownership(
    parsed: Dict[str, Any],
    script: str,
    in_memory_graph: Optional[Any],
) -> None:
    """Validates that asserted owners in the script do not conflict with verified GraphDB ownership."""
    if in_memory_graph is None:
        return
    owners = extract_verified_owners(in_memory_graph)
    if not owners:
        return
    script_lower = script.lower()
    if not any(w in script_lower for w in ("lead", "owns", "responsible")):
        return
    for target, valid_owners in owners.items():
        if target in script_lower and not any(o in script_lower for o in valid_owners):
            logger.warning(
                "C-20 ownership conflict detected for target '%s'; sanitizing to RACI clarification",
                target,
            )
            parsed["suggested_script"] = VERIFY_OWNERSHIP_FALLBACK_SCRIPT
            parsed["snap_cue"] = VERIFY_OWNERSHIP_FALLBACK_SNAP
            parsed["category"] = "ALIGNMENT"
            parsed["risk_trap"] = "Unverified ownership"
            parsed["risk_alert"] = "Unverified ownership"
            break


def _attach_inflection_telemetry(
    parsed: Dict[str, Any],
    inflection_event: Optional[Any],
) -> None:
    """Attaches inflection trigger telemetry if present."""
    if not inflection_event:
        return
    trigger = getattr(inflection_event, "trigger_type", None)
    parsed["inflection_trigger"] = trigger.value if hasattr(trigger, "value") else trigger
    parsed["trigger_reason"] = getattr(inflection_event, "reason", "")
    parsed["is_priority"] = getattr(inflection_event, "is_priority", False)


def _format_insightful_questions(parsed: Dict[str, Any]) -> None:
    """Formats insightful probing questions if not present."""
    if "insightful_questions" in parsed:
        return
    current_script = str(parsed.get("suggested_script") or "").strip()
    if current_script and "?" in current_script:
        parsed["insightful_questions"] = [current_script]
    elif current_script:
        parsed["insightful_questions"] = [f"Can we clarify: {current_script}?"]
    else:
        parsed["insightful_questions"] = []


def _budget_speaker_summary(parsed: Dict[str, Any]) -> str:
    raw_quote = parsed.get("speaker_summary_quotes") or parsed.get("suggested_script", "")
    if isinstance(raw_quote, list):
        raw_quote = " ".join(str(q).strip() for q in raw_quote if q)
    raw_quote = str(raw_quote).strip()
    stripped_quote = re.sub(r'^["\']+|["\']+$', "", raw_quote).strip()
    words_quote = stripped_quote.split()
    if len(words_quote) > MAX_SPEAKER_SUMMARY_WORDS:
        truncated_quote = _truncate_at_sentence_boundary(stripped_quote, MAX_SPEAKER_SUMMARY_WORDS)
        speaker_summary = f'"{truncated_quote}"'
    else:
        speaker_summary = f'"{stripped_quote}"'
    parsed["speaker_summary_quotes"] = speaker_summary
    return speaker_summary


def _extract_raw_bullets(parsed: Dict[str, Any]) -> List[str]:
    raw_bullets = parsed.get("supporting_details_bullets")
    if isinstance(raw_bullets, list):
        return raw_bullets
    candidates = [
        parsed.get("snap_cue"),
        parsed.get("suggested_script"),
        parsed.get("opportunity"),
        parsed.get("risk_trap"),
    ]
    extracted = [str(c).strip() for c in candidates if c and str(c).strip()]
    return extracted or ["Action item alignment required."]


def _budget_supporting_bullets(raw_bullets: List[str], budget: int) -> List[str]:
    budgeted: List[str] = []
    remaining = budget
    for b in raw_bullets:
        b_str = str(b).strip()
        if not b_str:
            continue
        words = b_str.split()
        if len(words) <= remaining:
            budgeted.append(b_str)
            remaining -= len(words)
        elif remaining > 0:
            budgeted.append(_truncate_at_sentence_boundary(b_str, remaining))
            break
        else:
            break
    if not budgeted and raw_bullets:
        fallback_word = raw_bullets[0].split()[0] if raw_bullets[0].split() else "Alignment"
        budgeted = [fallback_word]
    return budgeted


def _apply_response_budget(parsed: Dict[str, Any]) -> None:
    speaker_summary = _budget_speaker_summary(parsed)
    raw_bullets = _extract_raw_bullets(parsed)
    quote_words = _count_words(speaker_summary)
    budget = min(MAX_SUPPORTING_DETAILS_WORDS, max(0, MAX_TOTAL_CONTENT_WORDS - quote_words))
    budgeted_bullets = _budget_supporting_bullets(raw_bullets, budget)

    parsed["supporting_details_bullets"] = budgeted_bullets
    total_bullet_words = sum(_count_words(b) for b in budgeted_bullets)
    parsed["total_content_words"] = quote_words + total_bullet_words
    bullets_formatted = "\n".join(f"- {b}" for b in budgeted_bullets)
    parsed["formatted_response"] = (
        f"### Speaker Summary\n{speaker_summary}\n\n### Supporting Details\n{bullets_formatted}"
    )




def _is_valid_claim(claim: Dict[str, Any], valid_assertion_ids: List[str], live_turns: List[Dict[str, Any]]) -> bool:
    role = claim.get("role")
    source_kind = claim.get("source_kind")
    source_ref = claim.get("source_ref")
    span = claim.get("verbatim_span", "")

    if role == "FACT" and source_kind == "GRAPH_ASSERTION" and source_ref not in valid_assertion_ids:
        return False

    if role == "QUOTE" and source_kind == "LIVE_TURN":
        turn = next((t for t in live_turns if t.get("id") == source_ref), None)
        if not turn or span.lower() not in turn.get("text", "").lower():
            return False

    return True


def enforce_sentence_citations(
    parsed: Dict[str, Any],
    verified_assertions: Optional[List[Dict[str, Any]]],
    rigor_tier: str = "VERIFIED",
    target: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Enforces that 100% of sentences/bullets/summaries in VERIFIED copilot answers cite
    or map to at least one valid, active, non-stale assertion.
    Unmapped sentences or bullets are dropped.
    """
    from .copilot_clause_verifier import enforce_deep_clause_verification

    if parsed.get("provider_mode") == "MOCK":
        assertions = verified_assertions or []
        parsed["cited_assertion_ids"] = [
            str(a.get("assertion_id"))
            for a in assertions
            if a.get("assertion_id")
        ][:2]
        parsed["rigor_tier"] = rigor_tier
        return parsed

    parsed = enforce_deep_clause_verification(
        parsed, verified_assertions, rigor_tier=rigor_tier, target=target
    )
    _apply_response_budget(parsed)
    return parsed


def _validate_parsed_claims(
    parsed: Dict[str, Any],
    script: str,
    valid_assertion_ids: List[str],
    live_turns: List[Dict[str, Any]],
) -> str:
    if "claims" not in parsed:
        return script
    claims = parsed.get("claims")
    if not isinstance(claims, list):
        parsed["claims"] = []
        return script
    valid_claims = [c for c in claims if isinstance(c, dict) and _is_valid_claim(c, valid_assertion_ids, live_turns)]
    parsed["claims"] = valid_claims
    if not valid_claims and "owns" in script.lower() and "?" not in script:
        parsed["suggested_script"] = "Who owns this?"
        return "Who owns this?"
    return script


def _filter_scoped_assertions(
    verified_assertions: Optional[List[Dict[str, Any]]],
    engagement_id: Optional[str],
    in_memory_graph: Optional[Any] = None,
    permitted_engagements: Optional[Set[str]] = None,
) -> Optional[List[Dict[str, Any]]]:
    if not engagement_id or not verified_assertions:
        return verified_assertions
    from .copilot_connection_policy import (
        discover_topologically_inherited_engagements,
        is_assertion_in_scope,
    )
    eff_permitted = set(permitted_engagements or ())
    if in_memory_graph is not None:
        eff_permitted |= discover_topologically_inherited_engagements(in_memory_graph, engagement_id)
    return [a for a in verified_assertions if is_assertion_in_scope(a, engagement_id, eff_permitted)]


def validate_copilot_feedback(
    parsed: Dict[str, Any],
    graph_context: Optional[str] = None,
    in_memory_graph: Optional[Any] = None,
    inflection_event: Optional[Any] = None,
    model_id: Optional[str] = None,
    valid_assertion_ids: Optional[List[str]] = None,
    live_turns: Optional[List[Dict[str, Any]]] = None,
    verified_assertions: Optional[List[Dict[str, Any]]] = None,
    rigor_tier: Optional[str] = None,
    engagement_id: Optional[str] = None,
    permitted_engagements: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(parsed, dict) or parsed.get("has_guidance") is not True:
        return None

    if _scan_for_secrets(parsed):
        logger.error("Audit Event: Secret pattern detected in copilot feedback; failing closed")
        return None

    script = str(parsed.get("suggested_script", ""))
    snap = str(parsed.get("snap_cue", ""))
    script = _validate_parsed_claims(parsed, script, valid_assertion_ids or [], live_turns or [])

    script, snap = _check_c19_career_firewall(parsed, script, snap)
    script, snap = _check_c20_commitments(parsed, script, snap)
    script, snap = _check_energy_domain_integrity(parsed, script, snap)
    _check_c20_graph_ownership(parsed, script, in_memory_graph)
    _attach_inflection_telemetry(parsed, inflection_event)
    _format_insightful_questions(parsed)

    scoped_asrt = _filter_scoped_assertions(
        verified_assertions, engagement_id, in_memory_graph=in_memory_graph, permitted_engagements=permitted_engagements
    )
    if scoped_asrt is not None or rigor_tier == "VERIFIED":
        parsed = enforce_sentence_citations(
            parsed,
            scoped_asrt,
            rigor_tier=rigor_tier or "VERIFIED",
        )
    else:
        _apply_response_budget(parsed)

    from .copilot_clause_verifier import apply_suggestion_labeling
    apply_suggestion_labeling(parsed, set(parsed.get("cited_assertion_ids", [])))

    if graph_context:
        parsed["graph_context"] = graph_context

    return parsed
