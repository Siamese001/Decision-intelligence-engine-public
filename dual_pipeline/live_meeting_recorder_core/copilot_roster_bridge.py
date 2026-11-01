"""
dual_pipeline/live_meeting_recorder_core/copilot_roster_bridge.py

Bridges live meeting roster participants (from metadata.json) into the in-memory graph
topology, inverted search index, and attendee affinity sets.
Provides domain role synonym traversal mapping "tech owner", "tech lead", and "SWE"
to canonical technical anchors and participant engineering titles.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge")

DOMAIN_ROLE_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "tech owner": ("technical_anchor", "tech_anchor", "engineering_lead", "technical_lead", "swe"),
    "technical owner": ("technical_anchor", "tech_anchor", "engineering_lead", "technical_lead"),
    "tech lead": ("technical_anchor", "technical_lead", "engineering_lead", "swe"),
    "technical lead": ("technical_anchor", "technical_lead", "engineering_lead"),
    "product owner": ("product_lead", "business_lead", "project_development_lead", "owner"),
    "delivery lead": ("engagement_manager", "project_manager", "pm", "delivery"),
    "executive sponsor": ("sponsor", "vp", "director", "executive"),
    "architect": ("enterprise_architect", "lead_architect", "technical_anchor", "solution_architect"),
    "tech": ("technical", "technical_anchor", "technology"),
    "owner": ("technical_anchor", "anchor", "lead", "owner"),
    "swe": ("engineer", "developer", "full stack swe", "full stack"),
    "full stack": ("full stack swe", "engineer", "developer"),
}


def _tokenize_roster_text(text: str) -> Set[str]:
    raw = re.findall(r"\b[A-Za-z0-9_\-]{3,}\b|\b[A-Z0-9_\-]{2}\b", text)
    tokens = {w.lower() for w in raw if not w.startswith("-")}
    for t in list(tokens):
        if "_" in t or "-" in t:
            sub = re.findall(r"\b[A-Za-z0-9]{3,}\b|\b[A-Z0-9]{2}\b", t)
            tokens.update(w.lower() for w in sub)
    return tokens


def _parse_dict_entry(entry: Dict[str, Any], default_org: str) -> Optional[Dict[str, str]]:
    name = str(entry.get("name") or "").strip()
    role = str(entry.get("role") or "").strip()
    org = str(entry.get("org") or entry.get("organization") or default_org).strip()
    return {"name": name, "role": role, "org": org} if name else None


def _parse_string_entry(entry: str, default_org: str) -> Optional[Dict[str, str]]:
    clean = entry.strip()
    if not clean:
        return None
    if "(" in clean:
        name, details = clean.split("(", 1)
        name, details = name.strip(), details.rstrip(")").strip()
        parts = [p.strip() for p in details.split(",") if p.strip()]
        return {"name": name, "role": parts[0] if parts else "", "org": parts[1] if len(parts) > 1 else default_org} if name else None
    return {"name": clean, "role": "", "org": default_org}


def parse_attendee_entry(entry: Any, default_org: str = "") -> Optional[Dict[str, str]]:
    """Normalizes an individual roster entry into a structured dictionary."""
    if isinstance(entry, dict):
        return _parse_dict_entry(entry, default_org)
    return _parse_string_entry(entry, default_org) if isinstance(entry, str) else None


def _parse_receipt_attendees(rcpt: Any) -> List[Dict[str, str]]:
    conf = rcpt.get("confirmed_attendees") if isinstance(rcpt, dict) else None
    return [att for c in conf if (att := parse_attendee_entry(c)) is not None] if isinstance(conf, list) else []


def _parse_dict_attendees(raw_att: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        att
        for org_group, entries in raw_att.items()
        if isinstance(entries, list)
        for e in entries
        if (att := parse_attendee_entry(e, default_org=str(org_group))) is not None
    ]


def _parse_list_attendees(raw_att: List[Any]) -> List[Dict[str, str]]:
    return [att for e in raw_att if (att := parse_attendee_entry(e)) is not None]


def parse_attendees_from_metadata(meta: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extracts confirmed attendees from meeting metadata dictionary."""
    if not isinstance(meta, dict):
        return []
    combined = _parse_receipt_attendees(meta.get("attendee_confirmation_receipt"))
    raw_att = meta.get("attendees")
    if isinstance(raw_att, dict):
        combined.extend(_parse_dict_attendees(raw_att))
    elif isinstance(raw_att, list):
        combined.extend(_parse_list_attendees(raw_att))
    seen: Set[str] = set()
    deduped: List[Dict[str, str]] = []
    for att in combined:
        if (key := att["name"].lower()) not in seen:
            seen.add(key)
            deduped.append(att)
    return deduped


def _try_parse_manifest_file(mf: Path) -> List[Dict[str, str]]:
    if not mf.is_file():
        return []
    try:
        m_data = json.loads(mf.read_text(encoding="utf-8"))
        if isinstance(m_data, dict) and "attendees" in m_data:
            return parse_attendees_from_metadata(m_data)
    except Exception:
        pass
    return []


def _read_first_manifest_attendees(cand_dirs: List[Path]) -> List[Dict[str, str]]:
    for d in cand_dirs:
        for mf in [d / "session_manifest.json", *sorted(d.glob("*_manifest.json"))]:
            res = _try_parse_manifest_file(mf)
            if res:
                return res
    return []


def _apply_hint_entry_to_roster(h: Dict[str, Any], combined: List[Dict[str, str]], by_key: Dict[str, Dict[str, str]]) -> None:
    spk = str(h.get("speaker_name") or h.get("speaker") or "").strip()
    if not spk:
        return
    meta_h = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
    role_h, org_h = str(h.get("role") or meta_h.get("role") or "").strip(), str(h.get("org") or meta_h.get("org") or "").strip()
    k = spk.lower()
    if k in by_key:
        if role_h:
            by_key[k]["role"] = role_h
        if org_h:
            by_key[k]["org"] = org_h
    elif role_h or h.get("status") in ("APPLIED", "RECONCILED"):
        entry = {"name": spk, "role": role_h, "org": org_h}
        combined.append(entry)
        by_key[k] = entry


def _read_hint_file_entries(hp: Path) -> List[Dict[str, Any]]:
    if not hp.is_file():
        return []
    try:
        h_list = json.loads(hp.read_text(encoding="utf-8"))
        return [h for h in h_list if isinstance(h, dict)] if isinstance(h_list, list) else []
    except Exception:
        return []


def _overlay_hint_files(cand_dirs: List[Path], combined: List[Dict[str, str]]) -> None:
    by_key = {a["name"].lower(): a for a in combined}
    for d in cand_dirs:
        for fn in ("speaker_hints.json", "speaker_hints_queue.json"):
            for h in _read_hint_file_entries(d / fn):
                _apply_hint_entry_to_roster(h, combined, by_key)


def load_session_attendees(output_dir: Optional[Path]) -> List[Dict[str, str]]:
    """Loads and parses attendee roster from metadata.json, session manifests, and live speaker_hints.json."""
    if not output_dir:
        return []
    out_p = Path(output_dir)
    combined: List[Dict[str, str]] = []
    meta_path = out_p / "metadata.json"
    if meta_path.is_file():
        try:
            combined.extend(parse_attendees_from_metadata(json.loads(meta_path.read_text(encoding="utf-8"))))
        except Exception as exc:
            logger.warning("Failed to load session attendees from %s: %s", meta_path, exc)
    cand_dirs = [out_p, *(d for d in (out_p / ".cache", out_p / "cache") if d.is_dir())]
    manifest_attendees = _read_first_manifest_attendees(cand_dirs)
    if manifest_attendees:
        by_name = {a["name"].lower(): a for a in combined}
        combined = [
            {"name": ma["name"], "role": ma.get("role") or by_name.get(ma["name"].lower(), {}).get("role", ""), "org": ma.get("org") or by_name.get(ma["name"].lower(), {}).get("org", "")}
            for ma in manifest_attendees
        ]
    _overlay_hint_files(cand_dirs, combined)
    return combined


_SPEAKER_NAME_ALIASES: Dict[str, Tuple[str, ...]] = {
    "ed": ("ed", "eduardo", "edward"), "eduardo": ("ed", "eduardo", "edward"), "edward": ("ed", "eduardo", "edward"),
    "dan": ("dan", "daniel"), "daniel": ("dan", "daniel"), "mitch": ("mitch", "mitchell"), "mitchell": ("mitch", "mitchell"),
    "mike": ("mike", "michael"), "michael": ("mike", "michael"), "chris": ("chris", "christopher"), "christopher": ("chris", "christopher"),
    "matt": ("matt", "matthew"), "matthew": ("matt", "matthew"), "dave": ("dave", "david"), "david": ("dave", "david"),
    "rob": ("rob", "bob", "robert"), "bob": ("rob", "bob", "robert"), "robert": ("rob", "bob", "robert"),
    "bill": ("bill", "will", "william"), "william": ("bill", "will", "william"), "tom": ("tom", "thomas"), "thomas": ("tom", "thomas"),
    "alex": ("alex", "alexander", "alexandra"), "alexander": ("alex", "alexander"), "jen": ("jen", "jennifer"), "jennifer": ("jen", "jennifer"),
    "steph": ("steph", "stephan", "stephane", "stephen", "stepfan", "stefan", "stefanos"),
    "stephan": ("steph", "stephan", "stephane", "stephen", "stepfan", "stefan", "stefanos"),
    "stephane": ("steph", "stephan", "stephane", "stephen", "stepfan", "stefan", "stefanos"),
    "stephen": ("steph", "stephan", "stephane", "stephen", "stepfan", "stefan", "stefanos"),
    "stepfan": ("steph", "stephan", "stephane", "stephen", "stepfan", "stefan", "stefanos"),
    "stefan": ("steph", "stephan", "stephane", "stephen", "stepfan", "stefan", "stefanos"),
    "stefanos": ("steph", "stephan", "stephane", "stephen", "stepfan", "stefan", "stefanos"),
    "jason": ("jason", "jay"), "jay": ("jason", "jay"),
    "jeff": ("jeff", "jeffrey", "jeffery"), "jeffrey": ("jeff", "jeffrey", "jeffery"), "jeffery": ("jeff", "jeffrey", "jeffery"),
    "kate": ("kate", "katie", "catherine", "cathryn", "katherine", "kathryn"), "cathryn": ("kate", "katie", "catherine", "cathryn", "katherine", "kathryn"),
}


def are_same_speaker_identity(name_a: Optional[str], name_b: Optional[str]) -> bool:
    """Returns True if two speaker strings refer to the same canonical person (handling nicknames and middle names)."""
    toks_a = [t for t in re.findall(r"[a-z]{2,}", (name_a or "").lower()) if t not in ("mr", "ms", "mrs", "dr")]
    toks_b = [t for t in re.findall(r"[a-z]{2,}", (name_b or "").lower()) if t not in ("mr", "ms", "mrs", "dr")]
    if not toks_a or not toks_b:
        return False
    if toks_a == toks_b:
        return True
    first_a, first_b = toks_a[0], toks_b[0]
    aliases_a = set(_SPEAKER_NAME_ALIASES.get(first_a, (first_a,)))
    if first_b not in aliases_a:
        return False
    if len(toks_a) == 1 or len(toks_b) == 1:
        return True
    if toks_a[-1] == toks_b[-1]:
        return True
    rem_a, rem_b = set(toks_a[1:]), set(toks_b[1:])
    return rem_a.issubset(rem_b) or rem_b.issubset(rem_a)


def find_matching_graph_node(name: str, nodes: Dict[str, Any]) -> Optional[str]:
    """Finds an existing person or entity node in the graph matching an attendee name."""
    name_low = name.lower()
    for nid, node in nodes.items():
        if isinstance(node, dict):
            cn_raw, dn_raw = str(node.get("canonical_name") or ""), str(node.get("display_name") or "")
            cn, dn = cn_raw.lower(), dn_raw.lower()
            if name_low in (cn, dn) or (len(name_low) > 3 and name_low in cn) or are_same_speaker_identity(name, cn_raw) or are_same_speaker_identity(name, dn_raw):
                return nid
    return None


def _create_virtual_attendee(name: str, role: str, org: str, topology_nodes: Dict[str, Any]) -> str:
    node_id = f"attendee:{re.sub(r'[^a-z0-9_]+', '_', name.lower()).strip('_')}"
    topology_nodes[node_id] = {
        "node_id": node_id, "canonical_name": name, "display_name": name,
        "node_type": "attendee", "role": role, "organization": org, "is_attendee": True,
    }
    return node_id


def _attach_meeting_role_assertion(
    name: str, role: str, org: str, node_id: str, now_iso: str,
    salient_assertions: Dict[str, List[Dict[str, Any]]], topology_assertions: Optional[Dict[str, Any]],
) -> None:
    asrt_id = f"asrt_attendee_{re.sub(r'[^a-z0-9_]+', '_', name.lower()).strip('_')}"
    has_explicit_role = bool(role and role.strip().lower() not in ("participant", "attendee", "unknown"))
    claim = f"{name} is an active meeting participant with role {role or 'Participant'} ({org or 'Apex Global Enterprises'})."
    asrt_dict: Dict[str, Any] = {
        "assertion_id": asrt_id, "subject_node_id": node_id, "predicate": "HAS_ROLE",
        "claim_text": claim, "object_node_id": role or "Participant", "object_literal": claim,
        "canonical_status": "ACTIVE", "status": "ACTIVE", "is_active": 1, "is_priority": False,
        "source_date_normalized": now_iso, "observed_at": now_iso, "source_date": now_iso,
        "source_id": "live_meeting_roster", "source_ids": ["source:live_meeting_roster"],
        "evidence_ids": ["ev_live_roster"], "primary_source_id": "live_meeting_roster",
        "engagement_id": "shared", "direction": "outbound", "related_node": role or "Participant",
    }
    existing = salient_assertions[node_id]
    existing[:] = [a for a in existing if a.get("assertion_id") != asrt_id]
    existing.insert(0, asrt_dict) if has_explicit_role else existing.append(asrt_dict)
    if isinstance(topology_assertions, dict):
        topology_assertions[asrt_id] = asrt_dict


def _index_attendee_tokens(tokens: Set[str], role: str, node_id: str, inverted_index: Dict[str, Set[str]]) -> None:
    for tok in tokens:
        inverted_index[tok].add(node_id)
    r_low = role.lower()
    if any(k in r_low for k in ("swe", "engineer", "developer", "architect", "tech lead", "technical lead")):
        inverted_index["tech_owner"].add(node_id)
        inverted_index["technical_anchor"].add(node_id)
    if "owner" in r_low:
        inverted_index["owner"].add(node_id)


def _seed_single_attendee(
    att: Dict[str, Any],
    topology_nodes: Dict[str, Any],
    inverted_index: Dict[str, Set[str]],
    attendee_tokens: Set[str],
    attendee_node_ids: Set[str],
    allowed_nodes: Set[str],
    salient_assertions: Dict[str, List[Dict[str, Any]]],
    now_iso: str,
    topology_assertions: Optional[Dict[str, Any]] = None,
) -> None:
    if not isinstance(att, dict):
        return
    name, role, org = str(att.get("name") or "").strip(), str(att.get("role") or "").strip(), str(att.get("org") or att.get("organization") or "").strip()
    if not name:
        return
    tokens = _tokenize_roster_text(f"{name} {role} {org}")
    attendee_tokens.update(tokens)
    matched_nid = find_matching_graph_node(name, topology_nodes)
    if not matched_nid:
        matched_nid = _create_virtual_attendee(name, role, org, topology_nodes)
    elif isinstance(topology_nodes.get(matched_nid), dict):
        topology_nodes[matched_nid]["is_attendee"] = True
        if role:
            topology_nodes[matched_nid]["role"] = role
        if org:
            topology_nodes[matched_nid]["organization"] = org
    _attach_meeting_role_assertion(name, role, org, matched_nid, now_iso, salient_assertions, topology_assertions)
    allowed_nodes.add(matched_nid)
    attendee_node_ids.add(matched_nid)
    _index_attendee_tokens(tokens, role, matched_nid, inverted_index)


def seed_attendee_identities(
    attendees: List[Dict[str, Any]],
    topology_nodes: Dict[str, Any],
    inverted_index: Dict[str, Set[str]],
    attendee_tokens: Set[str],
    attendee_node_ids: Set[str],
    allowed_nodes: Set[str],
    salient_assertions: Dict[str, List[Dict[str, Any]]],
    topology_assertions: Optional[Dict[str, Any]] = None,
) -> None:
    """Seeds live meeting attendees from metadata.json into the in-memory graph topology."""
    now_iso = datetime.now(timezone.utc).isoformat()
    for att in attendees:
        _seed_single_attendee(
            att, topology_nodes, inverted_index, attendee_tokens,
            attendee_node_ids, allowed_nodes, salient_assertions, now_iso, topology_assertions
        )


def expand_role_query_tokens(tokens: Set[str]) -> Set[str]:
    """Expands query tokens using domain role and technical anchor synonyms."""
    expanded = set(tokens)
    for t in tokens:
        if t in DOMAIN_ROLE_SYNONYMS:
            expanded.update(DOMAIN_ROLE_SYNONYMS[t])
    if "tech" in tokens and "owner" in tokens:
        expanded.update(DOMAIN_ROLE_SYNONYMS.get("tech owner", ()))
    if "technical" in tokens and "owner" in tokens:
        expanded.update(DOMAIN_ROLE_SYNONYMS.get("technical owner", ()))
    if "tech" in tokens and "lead" in tokens:
        expanded.update(DOMAIN_ROLE_SYNONYMS.get("tech lead", ()))
    if "full" in tokens and "stack" in tokens:
        expanded.update(DOMAIN_ROLE_SYNONYMS.get("full stack", ()))
    return expanded


def score_assertion_synonyms(comb: str, kw_lows: List[str]) -> float:
    """Computes relevance score boost when assertion matches domain role synonyms."""
    score = 0.0
    for kw in kw_lows:
        synonyms = DOMAIN_ROLE_SYNONYMS.get(kw, ())
        for syn in synonyms:
            if re.search(r"\b" + re.escape(syn) + r"\b", comb):
                score += 1.5
                break
    return score


def _compute_node_recency_mult(nid: str, node_latest_epoch: Optional[Dict[str, float]], reference_epoch: float) -> float:
    if not node_latest_epoch or reference_epoch <= 0.0:
        return 1.0
    latest = float(node_latest_epoch.get(nid, 0.0) or 0.0)
    if latest <= 0.0:
        return 1.0
    age_days = max(0.0, (reference_epoch - latest) / 86400.0)
    return 0.70 + 0.30 * max(0.25, pow(0.5, age_days / 45.0))


def rank_nodes_by_tokens(
    matched_nodes: Set[str],
    query_tokens: Set[str],
    nodes: Dict[str, Any],
    inverted_index: Dict[str, Set[str]],
    token_idf: Dict[str, float],
    node_latest_epoch: Optional[Dict[str, float]] = None,
    reference_epoch: float = 0.0,
) -> List[str]:
    """Ranks matched graph nodes by token overlap, IDF score, and recent assertion activity."""
    def _score(nid: str) -> float:
        sc = 0.0
        nd = nodes.get(nid, {}) if isinstance(nodes, dict) else {}
        cn = str(nd.get("canonical_name") or "").lower()
        dn = str(nd.get("display_name") or "").lower()
        for t in query_tokens:
            if nid in inverted_index.get(t, set()):
                sc += token_idf.get(t, 1.0)
            if t in cn or t in dn:
                sc += 5.0
        return sc * _compute_node_recency_mult(nid, node_latest_epoch, reference_epoch)
    return sorted(matched_nodes, key=_score, reverse=True)


_LIVE_ONLY_DIRECTIVE_RE = re.compile(
    r"\b(?:(?:live|current|active|meeting|call)\s+transcripts?\s+only|"
    r"only\s+(?:from|based\s+on|use|using)\s+(?:the\s+)?(?:live\s+|current\s+|active\s+)?transcripts?|"
    r"(?:do\s+not|don['’]?t|never|no)\s+(?:look\s+at|use|query|search|check|include|pull\s+from)?\s*(?:the\s+)?(?:nee\s+|enterprise\s+|knowledge\s+)?graph|"
    r"(?:ignore|without|skip|exclude)\s+(?:the\s+)?(?:nee\s+|enterprise\s+|knowledge\s+)?graph)\b",
    re.IGNORECASE,
)


def is_live_transcript_only_directive(req: Optional[str]) -> bool:
    """Returns True when the operator explicitly restricts Copilot to the live transcript and forbids graph lookup."""
    return bool(req and _LIVE_ONLY_DIRECTIVE_RE.search(req))


_WORD_COUNT_MAP: Dict[str, int] = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
_META_DIRECTIVE_PATTERNS = (
    re.compile(r"^(?:[-/]{1,2}\s*)?(?:copilot|query|prompt)\b[:\s-]*", re.IGNORECASE),
    re.compile(r"(?:^|\s)[-/]{0,2}\bcopilot\b[:\s-]*", re.IGNORECASE),
    _LIVE_ONLY_DIRECTIVE_RE,
    re.compile(r"(?:\s*[-–—]\s*)?\b(?:review(?:ing)?\s+)?(?:in\s+|from\s+|over\s+)?(?:the\s+)?last\s+\d+\s*(?:mins?|minutes?|secs?|seconds?)(?:\s+in\s+detail)?\b", re.IGNORECASE),
    re.compile(r"\b(?:review(?:ing)?\s+|ingest(?:ing)?\s+|refresh(?:ing)?\s+)?(?:the\s+|this\s+)?(?:whole|entire|full)\s+(?:(?:live|liver|call|meeting|session|active|current)\s+)*transcripts?(?:\s+and\s+synthesize)?\b", re.IGNORECASE),
    re.compile(r"\b(?:based\s+on|from|in|on|use|using|ingest(?:ing)?)\s+(?:the\s+|this\s+)?(?:active\s+|current\s+|live\s+|liver\s+|whole\s+|full\s+|entire\s+|recent\s+)+transcripts?(?:\s+only)?\b", re.IGNORECASE),
    re.compile(r"\b(?:(?:live|liver|whole|entire|full|active|current)\s+)+transcripts?(?:\s+only)?\b", re.IGNORECASE),
    re.compile(r"\brefresh\s+this\s+discussion(?:\s+\d+%)?\b", re.IGNORECASE),
    re.compile(r"\b(?:on\s+)?how\s+we\s+can\s+work\s+(?:better\s+)?together\b", re.IGNORECASE),
    re.compile(r"\bsynthesize\s+(?:for\s+)?(?:the\s+)?(?:next\s+)?(?:best\s+)?(?:\d+\s+|one\s+|two\s+|three\s+|four\s+|five\s+)?(?:comments?|questions?|questiions?|points?|insights?)?\b", re.IGNORECASE),
    re.compile(r"\b(?:for\s+)?(?:the\s+)?(?:next\s+(?:best\s+|two\s+|three\s+|\d+\s+)*|most\s+(?:intellectual|intelligent)\s+|novel\s+|new\s+)(?:strategic\s+)?(?:speaker\s+)?(?:questions?|questiions?|comments?|points?|actions?|insights?|responses?)(?:\s+(?:i\s+can\s+make|to\s+drive\s+discussion\s+forward))?\b", re.IGNORECASE),
    re.compile(r"(?:<\s*\d+\s*words?\b|\b\d+\s*iq\b|\bno\s+repeating(?:\s+back)?(?:\s+what\s+they\s+said)?\b|\bdon['’]?t\s+repeat(?:\s+back)?\b)", re.IGNORECASE),
    re.compile(r"\b(?:anything\s+i\s+should\s+contribute(?:\s+high\s+signal)?(?:\s+to\s+this\s+call)?|high\s+signal\s+to\s+this\s+call|contribute\s+to\s+this\s+call)\b", re.IGNORECASE),
    re.compile(r"\b(?:to\s+drive\s+discussion\s+forward|in\s+detail)\b", re.IGNORECASE),
)


def strip_copilot_prompt_meta(req: str) -> str:
    """Strips CLI flags, live-transcript meta-references, and temporal window directives."""
    cleaned = req.strip()
    for pat in _META_DIRECTIVE_PATTERNS:
        cleaned = pat.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" -–—,.;:")


def parse_prompt_window_and_count(req: str) -> Tuple[Optional[float], Optional[int], bool]:
    """Extracts (window_seconds, requested_count, has_explicit_intent) from natural prompt."""
    low = (req or "").lower()
    win_sec: Optional[float] = None
    if re.search(r"\b(?:whole|entire|full)\s+(?:(?:live|liver|call|meeting|session|active|current)\s+)*transcripts?\b", low):
        win_sec = 86400.0
    elif m_win := re.search(r"\blast\s+(\d+)\s*(mins?|minutes?|secs?|seconds?)\b", low):
        win_sec = float(m_win.group(1)) * (60.0 if m_win.group(2).startswith("m") else 1.0)

    req_count: Optional[int] = None
    if m_cnt := re.search(r"\b(?:next\s+(?:best\s+)?|top\s+|for\s+(?:next\s+)?|best\s+)(\d+|one|two|three|four|five)\s+(?:best\s+)?(?:questi+ons?|comments?|points?|actions?)\b", low):
        req_count = _WORD_COUNT_MAP.get(m_cnt.group(1))
    elif re.search(r"\b(?:next\s+(?:best\s+)?|most\s+(?:intellectual|intelligent)\s+|novel\s+)(?:strategic\s+)?(?:speaker\s+)?(?:point|questi+on|comment|insight)\b", low) and not re.search(r"\b(?:questi+ons|comments|points|insights)\b", low):
        req_count = 1

    has_explicit = bool(re.search(
        r"\b(?:questi+ons?|nbq|actions?|commitments|comments?|points?|insights?|novel|react|respond|reply|gaps?|conflicts?|contradictions?|connect|multi-hop|transcripts?|synthesize|contribute|high\s+signal)\b",
        low,
    ))
    return win_sec, req_count, has_explicit

