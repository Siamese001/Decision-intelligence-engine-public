"""
dual_pipeline/transcript_pipeline_core/statement_filter.py

Modality-Calibrated Statement Filtering for Stage 03 Payload Optimization.
Pre-filters conversational small talk, clothing/weather banter, and social chatter
from raw transcript turns, extracting high-signal L0 technical and architectural
statements and preserving attendee rosters for Stage 03 Knowledge Graph extraction.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_STAGE03_MAX_BUDGET_CHARS = 22000

TECH_KEYWORDS = frozenset({
    "terraform", "aws", "gcp", "azure", "cloud", "infrastructure", "automation",
    "pipeline", "iac", "state", "security", "firewall", "compliance", "sox",
    "audit", "fedramp", "pci", "hipaa", "zero trust", "catalog", "service",
    "pattern", "patterns", "bespoke", "extensible", "operating model", "federated",
    "centralized", "hub and spoke", "provision", "self-service", "buccaneer",
    "servicenow", "backstage", "portal", "cmdb", "api", "unifier", "maximo",
    "repository", "repo", "github", "gitlab", "ci/cd", "deployment", "deploy",
    "kubernetes", "k8s", "cluster", "container", "docker", "workstream", "project",
    "architecture", "architectural", "migration", "database", "sql", "oracle",
    "postgres", "dynamo", "lambda", "s3", "ec2", "vpc", "subnet", "network",
    "route53", "dns", "load balancer", "alb", "nlb", "iam", "role", "policy",
    "governance", "proposal", "decision", "action item", "timeline", "sow",
    "deliverable", "discovery", "factory", "devops", "engineer", "lead", "director",
    "manager", "enterprise", "apex", "apex_global", "partner_advisory", "authentication", "sso",
    "active directory", "azure ad", "okta", "ldap", "analytics", "telemetry",
    "observability", "monitoring", "datadog", "grafana", "prometheus", "splunk",
    "cloudwatch", "drift", "tagging", "cost", "finops", "budget", "scale",
    "scale-up", "resilience", "high availability", "disaster recovery", "dr",
    "rpo", "rto", "sla", "slo", "sli", "work order", "workflow", "intake",
    "front door", "segment a", "segment b", "mvp", "poc", "pilot", "production",
    "tool", "platform", "framework", "solution", "integration", "code", "script",
    "component", "module", "system", "process", "environment", "test", "qa", "release",
    "jarvis", "onenote", "gis", "jira", "lams", "p6", "primavera", "opc",
    "sharepoint", "sharefile", "documentum", "power bi", "powerbi", "dashboard",
    "archive", "tracker", "spreadsheet", "excel", "smartsheet", "title", "survey",
    "easement", "permit", "permitting", "crossing", "substation", "transmission",
    "solar", "wind", "crane", "decommissioning", "construction", "land",
})

BANTER_PHRASES = frozenset({
    "wearing jacket", "pocket square", "pocket doesn", "raining in miami", "just gnarly",
    "huge party", "fundraising season", "i pledge", "record the session",
    "turn off your video", "turn it on now", "please don't send me back",
    "take care. bye. hello", "about time we'll see", "how are you doing today",
    "how many people make it back on time", "minnesota is great", "lunch",
    "loud music", "event back there", "partying", "weather", "still getting some",
    "those are wednesday", "good afternoon", "good morning", "hi, how are you",
    "love minnesota", "moved from minnesota", "mute button", "can you hear me",
    "see my screen", "share my screen", "give our team a couple more minutes",
    "how is it going", "nice to see everybody", "nice to meet you", "j. crew",
    "quarter zip", "peter millar", "how are you doing", "i like that color",
    "cranberry", "you work out too much",
})

ROLE_INDICATORS = frozenset({
    "my name is", "i'm the", "i am the", "years with", "lead", "engineer",
    "director", "manager", "principal", "head of", "vice president", "vp",
    "consultant", "architect", "specialist", "analyst", "client partner",
})

_TURN_SPLIT_PATTERN = re.compile(
    r"\n(?=(?:###\s*\[\d{1,2}:\d{2}(?::\d{2})?(?:[–\-\—]\d{1,2}:\d{2}(?::\d{2})?)?\].*|"
    r"\*\*\[\d{1,2}:\d{2}(?::\d{2})?(?:[–\-\—]\d{1,2}:\d{2}(?::\d{2})?)?\].*|"
    r"\[\d{1,2}:\d{2}(?::\d{2})?(?:[–\-\—]\d{1,2}:\d{2}(?::\d{2})?)?\]\s*(?:\*\*[^\n*]+?\*\*|[A-Za-z0-9][^\n:]+):?|"
    r"\*\*[A-Za-z0-9\[][^\n*]+?\*\*:?|"
    r"\[Participant\]:?))"
)
_HEADER_MATCH = re.compile(
    r"^(\*\*[^\n*]+?\*\*:?|\[Participant\]:?|###\s*\[[^\n\]]+\][^\n]*|\[\d{1,2}:\d{2}(?::\d{2})?(?:[–\-\—]\d{1,2}:\d{2}(?::\d{2})?)?\]\s*(?:\*\*[^\n*]+?\*\*|[A-Za-z0-9][^\n:]+):?)(?:\s*(.*))?$",
    re.DOTALL,
)

EVIDENCE_PATTERNS = frozenset({
    "decide", "decided", "decision", "agree", "agreed", "agreement", "approve",
    "approved", "approval", "commit", "committed", "commitment", "action item",
    "deadline", "deliverable", "milestone", "schedule", "budget", "cost",
    "dollar", "target", "objective", "risk", "gap", "blocker", "mitigate",
    "confirmed", "priority", "requirement", "sign-off", "signed off", "status",
    "debrief", "pause", "paused", "lessons learned", "access", "connector",
    "permission", "conduit", "charter", "alert", "escalation", "accountability",
    "bid", "scope", "change order", "historical", "confidential", "confidentiality",
    "privileged", "discoverable", "recording", "toggle", "forecast", "forecasting",
    "purview", "gripe", "user-friendly", "maintained", "upload", "save", "submit",
    "naming", "folder", "repository", "sent it in the chat", "commercial terms",
    "technical details", "over email", "run this by", "not technical", "tech strategy",
})
_QUANT_METRIC_RE = re.compile(r"(?:\b\d+(?:\.\d+)?%?|\$[\d\.]+[kKmMbB]?|\b\d{1,2}/\d{1,2}/\d{2,4}\b)")

_TECH_REGEX = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(TECH_KEYWORDS, key=len, reverse=True)) + r")(?:s|es)?(?!\w)",
    re.IGNORECASE,
)
_ROLE_REGEX = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(ROLE_INDICATORS, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)
_EVIDENCE_REGEX = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(EVIDENCE_PATTERNS, key=len, reverse=True)) + r")(?:s|es)?(?!\w)",
    re.IGNORECASE,
)


def _is_turn_banter(body: str) -> bool:
    """Check if turn is purely short conversational banter."""
    bl = body.lower()
    if _ROLE_REGEX.search(bl) or _TECH_REGEX.search(bl) or _EVIDENCE_REGEX.search(bl):
        return False
    return any(bp in bl for bp in BANTER_PHRASES) and len(body.split()) < 30


def _score_sentence(sentence: str) -> Tuple[int, bool]:
    """Score a sentence by technical, evidence, and role relevance."""
    sl = sentence.lower()
    if any(bp in sl for bp in BANTER_PHRASES) and len(sentence.split()) < 15:
        return 0, False
    is_intro = bool(_ROLE_REGEX.search(sl))
    tech_hits = len(_TECH_REGEX.findall(sl))
    evidence_hits = len(_EVIDENCE_REGEX.findall(sl))
    metric_hits = 1 if _QUANT_METRIC_RE.search(sl) else 0
    score = (10 if is_intro else 0) + tech_hits + (evidence_hits * 2) + metric_hits
    return score, is_intro


def _filter_turn_dialogue(body: str, max_chars: int = 800) -> Tuple[str, int, bool]:
    """Extract and bound high-signal sentences from a dialogue turn."""
    sentences = re.split(r"(?<=[.!?])\s+", body)
    kept_s: List[str] = []
    total_score = 0
    has_intro = False
    for s in sentences:
        score, is_intro = _score_sentence(s)
        if score > 0 or is_intro:
            kept_s.append(s)
            total_score += score
            if is_intro:
                has_intro = True
        elif not any(bp in s.lower() for bp in BANTER_PHRASES) and len(sentences) <= 3 and len(s.split()) >= 5:
            kept_s.append(s)
            total_score += 1
    if not kept_s:
        return "", 0, False
    dialogue = " ".join(kept_s)
    if len(dialogue) > max_chars:
        dialogue = dialogue[:max_chars].rsplit(" ", 1)[0] + "..."
    return dialogue, total_score, has_intro


def _select_stratified_budget_entries(
    entries: List[Dict[str, Any]], max_budget_chars: int
) -> List[Dict[str, Any]]:
    """Select substantive entries with stratified temporal sampling across quarters."""
    if not entries:
        return []
    total_len = sum(e["len"] for e in entries)
    if total_len <= max_budget_chars:
        return entries

    n = len(entries)
    num_q = 4 if n >= 8 else 1
    q_size = n // num_q
    quarters = [entries[i * q_size : (i + 1) * q_size if i < num_q - 1 else n] for i in range(num_q)]

    selected: List[Dict[str, Any]] = []
    rem_budget = max_budget_chars

    # Always preserve section anchors first
    for e in entries:
        if e.get("is_section") and rem_budget >= e["len"]:
            selected.append(e)
            rem_budget -= e["len"]

    selected_set = {e["idx"] for e in selected}
    per_q = rem_budget // num_q if num_q > 0 else rem_budget
    leftover = 0

    for q in quarters:
        cur_q_budget = per_q + leftover
        spent_q = 0
        intros = [e for e in q if e.get("is_intro") and e["idx"] not in selected_set]
        others = [e for e in q if not e.get("is_intro") and not e.get("is_section") and e["idx"] not in selected_set]
        others.sort(key=lambda x: x["score"], reverse=True)
        for cand in intros + others:
            if spent_q + cand["len"] <= cur_q_budget and rem_budget >= cand["len"]:
                selected.append(cand)
                selected_set.add(cand["idx"])
                spent_q += cand["len"]
                rem_budget -= cand["len"]
        leftover = max(0, cur_q_budget - spent_q)

    # Fill remaining budget from highest-scoring unselected entries across all quarters
    if rem_budget > 200:
        unselected = [e for e in entries if e["idx"] not in selected_set and not e.get("is_section")]
        unselected.sort(key=lambda x: x["score"], reverse=True)
        for cand in unselected:
            if rem_budget >= cand["len"]:
                selected.append(cand)
                selected_set.add(cand["idx"])
                rem_budget -= cand["len"]

    selected.sort(key=lambda x: x["idx"])
    return selected


def filter_stage03_statement_payload(
    transcript: str,
    metadata: Optional[Dict[str, Any]] = None,
    max_budget_chars: int = DEFAULT_STAGE03_MAX_BUDGET_CHARS,
) -> str:
    """
    Filters raw turns for Stage 03 to include only L0-routed technical statements
    and attendee rosters, eliminating conversational small talk and reducing tokens.
    """
    if not transcript or not transcript.strip():
        return ""

    meta = metadata or {}
    effective_budget = max_budget_chars
    if meta:
        if "max_budget_chars" in meta:
            effective_budget = int(meta["max_budget_chars"])
        elif "stage03_max_budget_chars" in meta:
            effective_budget = int(meta["stage03_max_budget_chars"])
        elif meta.get("proportional_payload_budget") or meta.get("allow_extended_payload"):
            effective_budget = min(90000, max(max_budget_chars, int(len(transcript) * 0.75)))

    if effective_budget <= 0:
        return ""

    is_doc_or_notes = bool(meta.get("notes_mode") or meta.get("skip_output_1") or meta.get("output3_only") or meta.get("is_document") or meta.get("modality") in ("DOCUMENT", "MEETING_NOTES"))
    if is_doc_or_notes:
        return transcript if len(transcript) <= effective_budget else transcript[:effective_budget]

    if len(transcript) <= effective_budget and not any(bp in transcript.lower() for bp in BANTER_PHRASES):
        return transcript

    preamble, body = "", transcript
    for marker in ("## Forensic Transcript", "## Transcript"):
        if marker in transcript:
            preamble, sep, body = transcript.partition(marker)
            preamble = preamble + sep + "\n\n"
            break

    body = re.sub(r"###\s*(\[\d{1,2}:\d{2}(?::\d{2})?(?:[–\-\—]\d{1,2}:\d{2}(?::\d{2})?)?\])[ \t]*\n+\s*(?=\*\*)", r"\1 ", body)
    blocks = [b for b in _TURN_SPLIT_PATTERN.split(body) if b and b.strip()]
    if len(blocks) <= 1:
        if not preamble and len(transcript) <= effective_budget:
            return transcript
        # Avoid slicing mid-word if fallback needed
        truncated = body[:effective_budget].rsplit("\n", 1)[0]
        return (preamble + truncated).strip()

    entries: List[Dict[str, Any]] = []
    for idx, b in enumerate(blocks):
        b_clean = b.strip()
        m = _HEADER_MATCH.match(b_clean)
        if m:
            hdr = m.group(1).strip()
            dial = (m.group(2) or "").strip()
        else:
            lines = [line.strip() for line in b_clean.split("\n") if line.strip()]
            hdr = lines[0] if lines else ""
            dial = " ".join(lines[1:]) if len(lines) > 1 else ""

        if hdr.startswith("###"):
            entries.append({
                "idx": idx, "is_intro": True, "score": 100,
                "text": b_clean, "len": len(b_clean) + 2, "is_section": True,
            })
            continue

        if dial and _is_turn_banter(dial):
            continue

        filtered_d, score, is_intro = _filter_turn_dialogue(dial) if dial else ("", 0, False)
        if not filtered_d:
            continue
        entry_text = f"{hdr} {filtered_d}" if dial else hdr
        entries.append({
            "idx": idx, "is_intro": is_intro, "score": score,
            "text": entry_text, "len": len(entry_text) + 2, "is_section": False,
        })

    if not entries:
        return (preamble + body[:effective_budget].rsplit("\n", 1)[0]).strip()

    avail_budget = max(100, effective_budget - len(preamble)) if effective_budget > len(preamble) else effective_budget
    selected = _select_stratified_budget_entries(entries, avail_budget)
    return (preamble + "\n\n".join(s["text"] for s in selected)).strip()
