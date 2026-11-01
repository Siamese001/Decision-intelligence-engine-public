"""
dual_pipeline/live_meeting_recorder_core/copilot_intents.py

Modular evaluation and grounding verification across canonical Copilot intents.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from dual_pipeline.live_meeting_recorder_core.copilot_context import filter_assertions_for_utterance_topic
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from .c19_firewall import has_c19_marker, is_c19_quarantined_assertion
from .copilot_connection_policy import is_assertion_in_scope


class BlendedDialogueContext:
    """Combines live dialogue context model seeds and turns with explicit topic and target queries."""

    def __init__(
        self, dialogue: Any, target: Optional[str] = None, topic_query: Optional[str] = None, topic_keywords: Optional[List[str]] = None
    ) -> None:
        self.dialogue, self.target, self.topic_query, self.topic_keywords = dialogue, target, topic_query, (topic_keywords or [])

    def retrieval_seeds(self) -> List[str]:
        seeds = [x for x in (self.target, self.topic_query) if x] + list(self.topic_keywords)
        extra = self.dialogue.retrieval_seeds() if hasattr(self.dialogue, "retrieval_seeds") else ([self.dialogue] if isinstance(self.dialogue, str) and self.dialogue else [])
        for s in extra:
            if s not in seeds:
                seeds.append(s)
        return seeds

    def focus_turns(self) -> List[Dict[str, Any]]:
        if hasattr(self.dialogue, "focus_turns"):
            return list(self.dialogue.focus_turns())[:20]
        return []

    def __str__(self) -> str:
        parts: List[str] = []
        if self.target:
            parts.append(self.target)
        if self.topic_query:
            parts.append(self.topic_query)
        if self.topic_keywords:
            parts.extend(self.topic_keywords)
        if hasattr(self.dialogue, "focus_turns"):
            focus = self.dialogue.focus_turns()
            if focus:
                parts.extend(f"{t.get('speaker', '')}: {t.get('text', '')}" for t in focus[:5])
        elif self.dialogue:
            parts.append(str(self.dialogue))
        return " ".join(parts).strip()


def eval_questions(
    retriever: CopilotGraphRetriever,
    dialogue: Any,
    topic_query: Optional[str] = None,
    topic_keywords: Optional[List[str]] = None,
    target: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    blended = BlendedDialogueContext(dialogue, target=target, topic_query=topic_query, topic_keywords=topic_keywords)
    assertions = retriever.get_next_questions(blended, max_questions=3)
    if not assertions:
        return "", [], False
    lines = ["Recommended Questions:"]
    for i, a in enumerate(assertions, 1):
        claim = a.get("claim_text", "")
        lines.append(f"{i}. Given {claim}, what is the current status and next step?")
    return "\n".join(lines), assertions, True


def eval_actions(
    retriever: CopilotGraphRetriever,
    dialogue: Any,
    topic_query: Optional[str] = None,
    topic_keywords: Optional[List[str]] = None,
    target: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    blended = BlendedDialogueContext(dialogue, target=target, topic_query=topic_query, topic_keywords=topic_keywords)
    assertions = retriever.get_next_actions(blended, max_actions=3)
    if not assertions:
        return "", [], False
    lines = ["Recommended Actions:"]
    for i, a in enumerate(assertions, 1):
        claim = a.get("claim_text", "")
        lines.append(f"{i}. Follow up on: {claim}")
    return "\n".join(lines), assertions, True


def _fallback_respond_live_assertions(
    assertions: List[Dict[str, Any]], dialogue: Any, target: Optional[str], selected_utterance: Optional[Any],
) -> List[Dict[str, Any]]:
    from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import (
        has_live_transcript_directive,
        merge_live_and_graph_assertions,
        synthesize_live_transcript_assertions,
    )
    raw_req = getattr(dialogue, "_raw_request", None) or getattr(getattr(dialogue, "dialogue", None), "_raw_request", None)
    non_stub = [a for a in assertions if not str(a.get("assertion_id", "")).startswith("asrt_attendee_")]
    if non_stub and not has_live_transcript_directive(raw_req):
        return assertions
    live_asrts = synthesize_live_transcript_assertions(dialogue, target=target, selected_utterance=selected_utterance)
    if not live_asrts:
        return assertions
    return merge_live_and_graph_assertions(live_asrts, assertions, raw_req, has_live_transcript_directive(raw_req))


def eval_respond(
    retriever: CopilotGraphRetriever, target: Optional[str], dialogue: Any, selected_utterance: Optional[Any] = None,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    person_ctx = retriever.get_person_context(target or "", dialogue)
    assertions = list(person_ctx.get("assertions", []))
    has_live = hasattr(dialogue, "focus_turns") and bool(dialogue.focus_turns())
    if has_live and not _is_unmapped_non_enterprise_session(retriever, assertions) and not [a for a in assertions if not str(a.get("assertion_id", "")).startswith("asrt_attendee_")]:
        assertions.extend(retriever.get_graph_context(dialogue)[:4])
    assertions = _fallback_respond_live_assertions(assertions, dialogue, target, selected_utterance)
    if not assertions and not person_ctx.get("found"):
        return "", [], False
    node_info = person_ctx.get("node_info", {})
    org = node_info.get("organization") or node_info.get("org", "External")
    role = node_info.get("role") or node_info.get("node_type", "Stakeholder")
    seen_line_claims: set[str] = set()
    claims: List[str] = []
    for a in assertions:
        c = str(a.get("claim_text") or a.get("object_literal") or "").strip()
        if c and c.lower() not in seen_line_claims:
            seen_line_claims.add(c.lower())
            claims.append(c)
        if len(claims) >= 3:
            break
    lines = [f"Advisory Coaching (Target: {target} | Org: {org} | Role: {role}):"] + [f"- {c}" for c in claims if c]
    return "\n".join(lines), assertions, True


def eval_graph_context(retriever: CopilotGraphRetriever, dialogue: Any) -> Tuple[str, List[Dict[str, Any]], bool]:
    assertions = retriever.get_graph_context(dialogue)
    if not assertions:
        return "", [], False
    lines = ["Verified Graph Knowledge:"]
    for a in assertions:
        lines.append(f"- {a.get('predicate')}: {a.get('claim_text')}")
    return "\n".join(lines), assertions, True


def eval_connect(retriever: CopilotGraphRetriever, dialogue: Any) -> Tuple[str, List[Dict[str, Any]], bool]:
    chains, assertions = retriever.get_connected_chains(dialogue, limit=5)
    if not chains or not assertions:
        return "", [], False
    lines = ["Connected Multi-Hop Knowledge:"]
    for c in chains:
        lines.append(f"- {c['chain_str']}")
        if c.get("last_claim"):
            lines.append(f"  * Fact: {c['last_claim']}")
    return "\n".join(lines), assertions, True


def _prepend_conflict_assertions(assertions: List[Dict[str, Any]], conflicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = {a.get("assertion_id") for a in assertions}
    items: List[Dict[str, Any]] = []
    for conflict in conflicts:
        for key in ("assertion_a", "assertion_b"):
            aid = conflict.get(key, {}).get("assertion_id")
            if aid and aid not in seen:
                items.append({**conflict[key], "live_directive_lock": True})
                seen.add(aid)
    return (items + assertions) if items else assertions


def eval_gaps(retriever: CopilotGraphRetriever, dialogue: Any) -> Tuple[str, List[Dict[str, Any]], bool]:
    assertions, has_prior_record = retriever.get_gaps(dialogue)
    conflicts = getattr(retriever, "_last_gap_conflicts", [])
    assertions = _prepend_conflict_assertions(assertions, conflicts)
    if not assertions:
        return "", [], False
    if not has_prior_record:
        return "No prior record of conflicting or changing commitments found in GraphDB.", assertions, True
    lines = ["Cross-Meeting Gap & Evolution Analysis:"]
    if conflicts:
        lines.append("[CONTRADICTION DETECTED]:")
        for c in conflicts:
            aid_a = c["assertion_a"].get("assertion_id", "ast-a")
            aid_b = c["assertion_b"].get("assertion_id", "ast-b")
            lines.append(f"  * {c['reason']} ({aid_a} vs {aid_b})")
        lines.append("")
        lines.append("Chronological Commitments:")
    for a in assertions:
        date_str = a.get("source_date") or a.get("observed_at") or "Prior Meeting"
        lines.append(f"- [{date_str}] {a.get('predicate')}: {a.get('claim_text')}")
    return "\n".join(lines), assertions, True


def _chain_matches_tokens(c: Dict[str, Any], search_tokens: Set[str]) -> bool:
    """Matches search tokens against chain textual fields and path elements."""
    chain_str = str(c.get("chain_str", "")).lower()
    last_claim = str(c.get("last_claim", "")).lower()
    path = [str(n).lower() for n in c.get("path", []) or c.get("nodes", [])]
    for tok in search_tokens:
        if tok in chain_str or tok in last_claim:
            return True
        for p in path:
            if tok in p:
                return True
    return False


def _collect_neighborhood_fallback(
    chains: Any,
    search_tokens: Set[str],
    salient_dict: Dict[str, Any],
    top_assertions: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Resolves matching neighborhood chains up to a limit of 5 assertions."""
    from .copilot_connect import _resolve_single_chain

    fallback: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for c in chains:
        if _chain_matches_tokens(c, search_tokens):
            chain_matches = _resolve_single_chain(c, salient_dict, top_assertions, seen_ids)
            fallback.extend(chain_matches)
            if len(fallback) >= 5:
                break
    return fallback


def _multihop_topic_fallback(
    retriever: CopilotGraphRetriever,
    topic_keywords: List[str],
    topic_query: Optional[str],
) -> List[Dict[str, Any]]:
    """Stage 2 multi-hop fallback using neighborhood chains to bridge disjoint entities."""
    search_query = topic_query or " ".join(topic_keywords)
    if not search_query.strip():
        return []

    try:
        _, chain_asrts = retriever.get_connected_chains(search_query, limit=5)
        if chain_asrts:
            return chain_asrts
    except Exception:
        pass

    store = getattr(retriever, "neighborhood_store", None)
    if not store or not getattr(store, "chains", None):
        return []

    search_tokens = {k.lower() for k in topic_keywords if len(k) > 1}
    if topic_query:
        search_tokens.update(topic_query.lower().split())

    salient_dict = getattr(retriever, "_salient_assertions", {})
    topology = getattr(retriever, "_topology", None)
    top_assertions = getattr(topology, "assertions", {}) if topology else {}

    return _collect_neighborhood_fallback(store.chains, search_tokens, salient_dict, top_assertions)


def _is_word_subset_match(label: str, t_words: Set[str]) -> bool:
    l_words = set(label.split())
    return bool(l_words and len(t_words) > len(l_words) and l_words.issubset(t_words))


def _is_entity_alias_match(
    t_words: Set[str],
    t_raw: str,
    c_name: str,
    d_name: str,
    aliases: List[str],
    nid: str,
) -> bool:
    if t_raw in (c_name, d_name, nid.lower()) or any(t_raw == a for a in aliases):
        return True
    return any(_is_word_subset_match(lbl, t_words) for lbl in (c_name, d_name, *aliases))


def _scan_matching_nodes(
    nodes: Dict[str, Any],
    cleaned_terms: List[Tuple[str, Set[str]]],
) -> Tuple[List[str], Set[str]]:
    resolved_nids: Set[str] = set()
    names: List[str] = []
    for nid, nd in nodes.items():
        if not isinstance(nd, dict):
            continue
        c_name = str(nd.get("canonical_name") or "").lower()
        d_name = str(nd.get("display_name") or "").lower()
        aliases = [str(a).lower() for a in nd.get("aliases", []) if isinstance(nd.get("aliases"), list)]

        if any(_is_entity_alias_match(tw, tr, c_name, d_name, aliases, nid) for tr, tw in cleaned_terms):
            resolved_nids.add(nid)
            real_c = str(nd.get("canonical_name") or nid)
            names.append(real_c)
    return names, resolved_nids


_NEE_ENTITY_EXPANSIONS = ("nee", "apex_global", "apex_global energy", "nea", "apex_global analytics", "nee.ai", "audience_next_era_analytics_ge301", "workstream_nee_ai_to_gemini_transition", "pattern_gemini_no_code_first")


def _resolve_graph_entity_aliases(
    retriever: CopilotGraphRetriever,
    terms: Iterable[str],
) -> Tuple[List[str], Set[str]]:
    """Resolves entity terms against graph topology canonical names and alias registry."""
    topology = getattr(retriever, "_topology", None)
    nodes = getattr(topology, "nodes", {}) if topology else {}
    if not isinstance(nodes, dict) or not terms:
        return list(terms), set()

    base_list = list(terms)
    low_set = {t.strip().lower() for t in base_list if t}
    if low_set & {"apex_global", "nee", "nea"}:
        base_list.extend(s for s in _NEE_ENTITY_EXPANSIONS if s not in low_set)
    cleaned_terms = [
        (t.strip().lower(), set(t.strip().lower().split()))
        for t in base_list
        if t and len(t.strip()) >= 2
    ]
    names, resolved_nids = _scan_matching_nodes(nodes, cleaned_terms)
    return list(dict.fromkeys(base_list + names)), resolved_nids


def _extract_resolved_entity_names(nodes: Dict[str, Any], resolved_nids: Set[str]) -> List[str]:
    names: List[str] = []
    for n in resolved_nids:
        nd = nodes.get(n)
        if not isinstance(nd, dict):
            continue
        cname = str(nd.get("canonical_name") or "").strip().lower()
        if len(cname) >= 4:
            names.append(cname)
    return names


def _matches_resolved_entity(a: Dict[str, Any], claim_low: str, resolved_nids: Set[str], entity_names: List[str]) -> bool:
    if a.get("subject_node_id") in resolved_nids or a.get("object_node_id") in resolved_nids:
        return True
    return any(en in claim_low for en in entity_names)


def _is_eligible_cross_workstream_assertion(
    a: Any, aid: str, seen: Set[str], eng_id: Optional[str], permitted: Optional[Set[str]], retriever: Optional[CopilotGraphRetriever],
) -> Optional[str]:
    if aid in seen or not isinstance(a, dict) or is_c19_quarantined_assertion(a):
        return None
    claim = str(a.get("claim_text") or a.get("object_literal") or "")
    if not claim or has_c19_marker(claim):
        return None
    ast_eng = str(a.get("engagement_id") or a.get("project_scope") or "")
    if ast_eng.startswith("quarantine:"):
        return None
    eff_perm = (set(permitted or ()) | {"project:project_lens", "project:pd_conduit"}) if eng_id == "project:project_nexus" else permitted
    if eng_id and not is_assertion_in_scope(a, eng_id, eff_perm):
        return None
    return None if str(a.get("canonical_status") or "").lower() in ("historical_candidate", "proposed", "retracted", "superseded", "rejected") else claim


def _collect_cross_workstream_entity_assertions(
    retriever: Optional[CopilotGraphRetriever],
    resolved_nids: Set[str],
    seen: Set[str],
) -> List[Dict[str, Any]]:
    topology = getattr(retriever, "_topology", None) if retriever else None
    eng_id = getattr(retriever, "engagement_id", None) if retriever else None
    if not resolved_nids or not topology:
        return []
    entity_names = _extract_resolved_entity_names(getattr(topology, "nodes", {}), resolved_nids)
    bridged: List[Dict[str, Any]] = []
    permitted = getattr(retriever, "permitted_engagements", None)
    for aid, a in getattr(topology, "assertions", {}).items():
        claim = _is_eligible_cross_workstream_assertion(a, aid, seen, eng_id, permitted, retriever)
        if claim and _matches_resolved_entity(a, claim.lower(), resolved_nids, entity_names):
            seen.add(aid)
            bridged.append({**a, "assertion_id": aid, "claim_text": claim})
    return bridged


def _merge_salient_assertions(
    candidates: List[Dict[str, Any]],
    resolved_nids: Set[str],
    salient_dict: Dict[str, Any],
    retriever: Optional[CopilotGraphRetriever] = None,
) -> List[Dict[str, Any]]:
    merged = list(candidates)
    seen = {str(a.get("assertion_id")) for a in merged if a.get("assertion_id")}
    for nid in resolved_nids:
        for sa in salient_dict.get(nid, []):
            aid = str(sa.get("assertion_id") or "")
            if aid and aid not in seen:
                seen.add(aid)
                merged.append(sa)
    merged.extend(_collect_cross_workstream_entity_assertions(retriever, resolved_nids, seen))
    return merged


def _build_topic_query_parts(
    target: Optional[str],
    topic_query: Optional[str],
    expanded_keywords: List[str],
    selected_utterance: Optional[Any],
) -> str:
    q_parts = [p for p in (target, topic_query) if p]
    for kw in expanded_keywords:
        if kw not in q_parts:
            q_parts.append(kw)
    if selected_utterance and hasattr(selected_utterance, "text"):
        q_parts.append(selected_utterance.text)
    return " ".join(q_parts)


def _prioritize_target_matches(
    filtered: List[Dict[str, Any]],
    all_expanded: List[str],
    expanded_keywords: List[str],
) -> List[Dict[str, Any]]:
    target_only = [k for k in all_expanded if k not in expanded_keywords]
    if not filtered or not target_only:
        return filtered
    both = filter_assertions_for_utterance_topic(filtered, target_only)
    if not both:
        return filtered
    both_ids = {a.get("assertion_id") for a in both}
    return both + [a for a in filtered if a.get("assertion_id") not in both_ids]


def _format_topic_respond_lines(target: Optional[str], topic_query: Optional[str], filtered: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]], bool]:
    lines = [f"Advisory Coaching (Target: {target} | Topic: {topic_query or 'General'}):"]
    for a in filtered[:5]:
        claim = a.get("claim_text") or a.get("object_literal") or ""
        if claim:
            lines.append(f"- {claim}")
    if len(lines) == 1:
        lines.append(f"- Anchor response to verified commitments and architectural bounds on {topic_query or 'discussion'}.")
    return "\n".join(lines), filtered[:5], True


def eval_topic_respond(
    retriever: CopilotGraphRetriever,
    target: Optional[str],
    selected_utterance: Optional[Any],
    topic_keywords: List[str],
    topic_query: Optional[str],
    dialogue: Any = None,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    utt_text = (getattr(selected_utterance, "speaker_summary", "") or getattr(selected_utterance, "text", "")) if selected_utterance else None
    topic_terms = [t for t in (topic_keywords + [topic_query]) if t]
    if not topic_terms and hasattr(dialogue, "retrieval_seeds"):
        topic_terms = [s for s in dialogue.retrieval_seeds() if not str(s).startswith("project:")]
    all_terms = [t for t in (topic_terms + [target]) if t]
    all_expanded, resolved_nids = _resolve_graph_entity_aliases(retriever, all_terms)
    expanded_keywords, _ = _resolve_graph_entity_aliases(retriever, topic_terms or all_terms)

    search_query = _build_topic_query_parts(target, topic_query, all_expanded, selected_utterance)
    candidates = retriever.retrieve_structured_assertions(search_query, max_results=15)
    candidates = _merge_salient_assertions(candidates, resolved_nids, getattr(retriever, "_salient_assertions", {}), retriever)

    filtered = _prioritize_target_matches(filter_assertions_for_utterance_topic(candidates, expanded_keywords, utt_text), all_expanded, expanded_keywords)
    if not filtered:
        filtered = _multihop_topic_fallback(retriever, expanded_keywords, topic_query)
    if not filtered:
        return eval_respond(retriever, target, dialogue, selected_utterance=selected_utterance) if (not topic_keywords and not topic_query and dialogue is not None) else ("", [], False)
    return _format_topic_respond_lines(target, topic_query, filtered)


def _extract_live_dialogue_domain_context(dialogue: Any) -> Tuple[Optional[str], List[str]]:
    if not dialogue or getattr(dialogue, "_offline_mode", False) or not hasattr(dialogue, "focus_turns") or not dialogue.focus_turns():
        return None, []
    from .copilot_utterance_resolver import _DOMAIN_SIGNAL_RE, _rank_focus_turns_by_substance
    top_turns = _rank_focus_turns_by_substance(dialogue)[:3]
    utt_text = " ".join(str(t.get("text") or "") for t in top_turns if t.get("text")).strip()
    dom_kws = list(dict.fromkeys(m.lower() for m in _DOMAIN_SIGNAL_RE.findall(utt_text) if len(m) >= 3))[:6]
    return (utt_text or None), dom_kws


def eval_graph_context_with_topic(
    retriever: CopilotGraphRetriever,
    topic_keywords: List[str],
    topic_query: Optional[str],
    dialogue: Any = None,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    utt_text, live_dom_kws = _extract_live_dialogue_domain_context(dialogue)
    blended_kws = list(dict.fromkeys(list(topic_keywords) + live_dom_kws))
    blended = BlendedDialogueContext(dialogue, topic_query=topic_query, topic_keywords=blended_kws)
    input_terms = [t for t in (topic_keywords + [topic_query]) if t]
    if not input_terms and hasattr(dialogue, "retrieval_seeds"):
        input_terms.extend(dialogue.retrieval_seeds())
    expanded_keywords, resolved_nids = _resolve_graph_entity_aliases(retriever, input_terms)

    candidates = retriever.retrieve_structured_assertions(blended, max_results=20)
    salient_dict = getattr(retriever, "_salient_assertions", {})
    candidates = _merge_salient_assertions(candidates, resolved_nids, salient_dict, retriever)

    filtered = filter_assertions_for_utterance_topic(candidates, expanded_keywords, utterance_text=utt_text)
    if not filtered:
        return "", [], False
    assertions = filtered[:20]
    lines = ["Verified Graph Knowledge:"]
    for a in assertions:
        lines.append(f"- {a.get('predicate')}: {a.get('claim_text') or a.get('object_literal')}")
    return "\n".join(lines), assertions, True


def _eval_respond_candidate(
    target: Optional[str], dialogue: Any, retriever: CopilotGraphRetriever,
    topic_keywords: Optional[List[str]], selected_utterance: Optional[Any], topic_query: Optional[str],
) -> Tuple[str, List[Dict[str, Any]], bool]:
    if topic_keywords or topic_query:
        return eval_topic_respond(retriever, target, selected_utterance, topic_keywords or [], topic_query, dialogue=dialogue)
    return eval_respond(retriever, target, dialogue, selected_utterance=selected_utterance)


def _eval_context_or_gaps_candidate(
    intent: str, target: Optional[str], dialogue: Any, retriever: CopilotGraphRetriever,
    topic_keywords: Optional[List[str]], topic_query: Optional[str],
) -> Optional[Tuple[str, List[Dict[str, Any]], bool]]:
    has_topic = bool(topic_keywords or topic_query)
    if intent == "graph-context":
        return eval_graph_context_with_topic(retriever, topic_keywords or [], topic_query, dialogue=dialogue) if has_topic else eval_graph_context(retriever, dialogue)
    if intent in ("gaps", "review"):
        ctx = BlendedDialogueContext(dialogue, target=target, topic_query=topic_query, topic_keywords=topic_keywords) if has_topic else dialogue
        return eval_gaps(retriever, ctx)
    return None


def eval_intent_candidates(
    intent: str, target: Optional[str], dialogue: Any, retriever: CopilotGraphRetriever,
    topic_keywords: Optional[List[str]], selected_utterance: Optional[Any], topic_query: Optional[str],
) -> Tuple[str, List[Dict[str, Any]], bool]:
    if intent in ("next-questions", "nbq", "challenge"):
        return eval_questions(retriever, dialogue, topic_query, topic_keywords, target)
    if intent == "next-actions":
        return eval_actions(retriever, dialogue, topic_query, topic_keywords, target)
    if intent == "respond":
        return _eval_respond_candidate(target, dialogue, retriever, topic_keywords, selected_utterance, topic_query)
    cg_res = _eval_context_or_gaps_candidate(intent, target, dialogue, retriever, topic_keywords, topic_query)
    if cg_res is not None:
        return cg_res
    return eval_connect(retriever, dialogue) if intent == "connect" else ("", [], False)


def verify_intent_candidates(assertions: List[Dict[str, Any]], dialogue: Any, retriever: CopilotGraphRetriever) -> List[Dict[str, Any]]:
    from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import CopilotGroundingVerifier
    result = CopilotGroundingVerifier().verify_and_ground(
        candidates=assertions, dialogue=dialogue, topology=getattr(retriever, "_topology", None),
        packet_graph_sha=retriever.graph_sha256, pinned_generation=retriever.generation,
    )
    retriever._last_rigor_tier = result.rigor_tier
    retriever._last_grounding_result = result
    return result.verified_assertions


def _is_unmapped_non_enterprise_session(retriever: CopilotGraphRetriever, assertions: List[Dict[str, Any]]) -> bool:
    if any(not str(nid).startswith("attendee:") and "amit" not in str(nid).lower() for nid in getattr(retriever, "_attendee_node_ids", set())):
        return False
    eng_slug = str(getattr(retriever, "engagement_id", "") or "").lower().replace("project:", "").replace("project_", "").strip("_")
    nodes = getattr(getattr(retriever, "_topology", None), "nodes", {})
    if eng_slug and isinstance(nodes, dict) and any(eng_slug in str(nid).lower() for nid in nodes):
        return False
    graph_asrts = [a for a in assertions if not a.get("is_live_turn") and not str(a.get("assertion_id", "")).startswith("asrt_attendee_")]
    if not graph_asrts or not any(str(a.get("provenance_scope") or "") == "ENTERPRISE_POLICY" for a in graph_asrts):
        return False
    if any(str(a.get("provenance_scope") or "") not in ("ENTERPRISE_POLICY", "") for a in graph_asrts):
        return False
    indexed = getattr(retriever, "_indexed_assertion_records", {})
    topo_asrts = getattr(getattr(retriever, "_topology", None), "assertions", None)
    return bool(indexed or topo_asrts) and not any(r.get("is_primary") or r.get("is_predecessor") for r in indexed.values())


def _allows_live_fallback(
    intent: str, dialogue: Any, topic_keywords: Optional[List[str]], selected_utterance: Optional[Any]
) -> bool:
    if intent in ("challenge", "next-questions", "nbq", "next-actions", "gaps", "review"):
        return True
    if intent == "respond":
        return not topic_keywords
    return intent == "graph-context" and not getattr(dialogue, "_offline_mode", False) and not topic_keywords


def _resolve_live_augmentation_mode(
    intent: str, dialogue: Any, assertions: List[Dict[str, Any]], ok: bool,
    retriever: Optional[CopilotGraphRetriever], topic_keywords: Optional[List[str]] = None, selected_utterance: Optional[Any] = None,
) -> Tuple[bool, bool, bool, Optional[str]]:
    from .copilot_roster_bridge import is_live_transcript_only_directive
    from .copilot_utterance_resolver import has_live_transcript_directive

    raw_req = getattr(dialogue, "_raw_request", None) or getattr(getattr(dialogue, "dialogue", None), "_raw_request", None)
    if intent == "connect" and not is_live_transcript_only_directive(raw_req):
        return False, False, False, raw_req
    has_live = hasattr(dialogue, "focus_turns") and bool(dialogue.focus_turns())
    live_only = is_live_transcript_only_directive(raw_req) or bool(retriever and has_live and _is_unmapped_non_enterprise_session(retriever, assertions))
    explicit_live = live_only or has_live_transcript_directive(raw_req)
    only_stubs = bool(assertions) and all(str(a.get("assertion_id", "")).startswith("asrt_attendee_") for a in assertions)
    is_live_ctx = intent in ("gaps", "review", "graph-context") and bool(raw_req and has_live and not getattr(dialogue, "_offline_mode", False))
    should_run = explicit_live or is_live_ctx or ((not ok or not assertions or only_stubs) and _allows_live_fallback(intent, dialogue, topic_keywords, selected_utterance))
    return should_run, live_only, explicit_live, raw_req


def _filter_graph_context_live_assertions(live_asrts: List[Dict[str, Any]], topic_keywords: List[str], assertions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from .copilot_context import _turn_matches_keyword
    kws = [k.lower() for k in topic_keywords if len(k) >= 2 and k.lower() not in ("nee", "apex_global", "nea", "partner_advisory", "apex", "energy")]
    matched_live = [a for a in live_asrts if any(_turn_matches_keyword(str(a.get("claim_text", "")).lower(), k) for k in kws)]
    return matched_live or (live_asrts if assertions else [])


def _maybe_augment_live_assertions(
    intent: str, target: Optional[str], dialogue: Any, selected_utterance: Optional[Any],
    topic_keywords: Optional[List[str]], body: str, assertions: List[Dict[str, Any]], ok: bool,
    retriever: Optional[CopilotGraphRetriever] = None,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    from .copilot_roster_bridge import is_live_transcript_only_directive
    from .copilot_utterance_resolver import merge_live_and_graph_assertions, synthesize_live_transcript_assertions

    should_run, live_only, explicit_live, raw_req = _resolve_live_augmentation_mode(
        intent, dialogue, assertions, ok, retriever, topic_keywords, selected_utterance
    )
    if not should_run:
        return body, assertions, ok
    live_asrts = synthesize_live_transcript_assertions(dialogue, target, selected_utterance, topic_keywords)
    if intent == "graph-context" and not explicit_live and topic_keywords:
        live_asrts = _filter_graph_context_live_assertions(live_asrts, topic_keywords, assertions)
    if not live_asrts:
        return ("", [], False) if is_live_transcript_only_directive(raw_req) else (body, assertions, ok)
    if live_only and not topic_keywords:
        for a in live_asrts:
            a["is_priority"] = a["live_directive_lock"] = True
        return "\n".join(f"- {a['claim_text']}" for a in live_asrts[:3]), live_asrts, True
    merged = merge_live_and_graph_assertions(live_asrts, assertions, raw_req, explicit_live)
    return (("\n".join(f"- {a['claim_text']}" for a in merged[:3])) if explicit_live else (body or "\n".join(f"- {a['claim_text']}" for a in merged[:3]))), merged, True


def evaluate_intent(
    intent: str, target: Optional[str], dialogue: Any, retriever: CopilotGraphRetriever,
    topic_keywords: Optional[List[str]] = None, selected_utterance: Optional[Any] = None, topic_query: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    """Dispatches intent to retriever and generates structured copilot body text."""
    body, assertions, ok = eval_intent_candidates(intent, target, dialogue, retriever, topic_keywords, selected_utterance, topic_query)
    body, assertions, ok = _maybe_augment_live_assertions(intent, target, dialogue, selected_utterance, topic_keywords, body, assertions, ok, retriever=retriever)
    retriever._last_initial_candidate_count = len(assertions)
    if ok and assertions:
        order_map = {str(a.get("assertion_id")): idx for idx, a in enumerate(assertions)}
        has_live = any(a.get("is_live_turn") for a in assertions)
        blended = BlendedDialogueContext(dialogue, target=target, topic_query=topic_query, topic_keywords=topic_keywords)
        assertions = verify_intent_candidates(assertions, blended, retriever)
        if has_live and len(assertions) > 1:
            assertions.sort(key=lambda a: order_map.get(str(a.get("assertion_id")), 999))
        ok = bool(assertions)
    return body, assertions, ok
