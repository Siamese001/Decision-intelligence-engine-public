#!/usr/bin/env python3
"""Validator, layman linguistics judge gate, and closed-loop feedback tool for Google x PartnerAdvisory Weekly Sync agendas.

Enforces accuracy-first partner alignment over restrictive formatting:
1. Flexible agenda body word budget <= 800 words (excluding '### Evidence & Graph Lineage' footer) and flexible bullet/sub-bullet counts so every action item and ask can be explained accurately without cramming.
2. Bullet and sub-bullet hierarchy without multi-sentence prose blocks, plus >= 12 words per substantive sub-bullet.
3. Trailing owner format: action/topic first, owner at the end of the sub-bullet (never leading 'Owner: ...').
4. Pure Weekly Partner Sync scope: only Google & PartnerAdvisory partner sync attendees; excludes Apex Global client personnel
   (e.g. Stefanos Georgiades), cross-meeting monthly enablement items (e.g. 'Legal session', Neha Dixit),
   commercial pricing leaks ($ figures, rate cards), offboarded aliases, and retracted/specious delay claims.
5. Two-stage Layman Readability & Linguistics Gate (Insider Shorthand Filter + Per-Bullet Clarity & Workstream Dedup Judge >= 85/100).
6. Closed-loop feedback persistence into JSON, out-of-tree mirror, HITLSQLiteStore, and learning.sqlite.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.google_partner_advisory_sync_core import (
    COMMERCIAL_PRICING_PATTERNS,
    COMPLETED_MARKER_RE,
    SPECIOUS_CLAIM_PATTERNS,
    check_open_and_completed_action_items,
    check_partner_directives_with_decay,
    check_subbullet_specificity,
    compute_directive_effective_weight,
    evaluate_stage_b_llm_semantic_judge,
    merge_cadence_ledgers,
    persist_system_learning_event,
    resolve_mirror_ledger_path,
    sync_ledger_with_active_graph,
    write_ledger_with_mirror,
)

DEFAULT_LEDGER_REL = Path("state/google_partner_advisory_sync_cadence_ledger.json")
MODEL_REGISTRY_REL = Path("config/runtime/model_registry.json")
LINEAGE_HEADER_RE = re.compile(r"^#{2,3}\s+Evidence\s*&\s*Graph\s+Lineage\b", re.IGNORECASE | re.MULTILINE)
BULLET_LINE_RE = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+(.+)$")
SUBBULLET_LINE_RE = re.compile(r"^\s{2,}(?:[-*+]|\d+\.)\s+(.+)$")
LEADING_OWNER_RE = re.compile(
    r"^\s{2,}(?:[-*+]|\d+\.)\s+(?:Alex|Rusty|Robson|Craig|Chad|Vani|Neha|Amit|Dan|Anya|Bryan|Shreyak|Rose|Chuck)\b[^:]{0,45}:",
    re.IGNORECASE,
)
TRAILING_OWNER_RE = re.compile(
    r"\((?:Owner|Owners|Lead|Leads)?[:\s]*[^)]*(?:Alex|Rusty|Robson|Craig|Chad|Vani|Amit|Dan|Anya|Bryan)[^)]*\)\.?$",
    re.IGNORECASE,
)

FORBIDDEN_LAYMAN_JARGON_TOKENS: Tuple[str, ...] = (
    "enterprise4u",
    "g eap",
    "jeep",
    "won_mobilizing",
    "identity-aware proxy",
    "entra id",
    "arcgis",
    "primavera p6",
    "ai-dlc",
    "cloud run",
    "vpc skill",
    "successfactors",
    "s/4hana",
    "aws kiro",
    "hub-and-spoke",
    "negotiation 5",
    "negotiation stage 5",
    "terraform",
    "vertex ai",
)

FORBIDDEN_CLIENT_AND_CROSS_MEETING_TOKENS: Tuple[str, ...] = (
    "stefanos georgiades",
    "stefanos g.",
    "stefanos",
    "daniel brankin",
    "Taylor Mercer",
    "jason nicholas",
    "jeremy cianella",
    "legal session",
    "legal ai",
    "neha dixit",
    "web eap",
    "early web access",
)

PLAIN_BUSINESS_OUTCOME_CUES: Tuple[str, ...] = (
    "funding",
    "march",
    "contract",
    "hr",
    "employee",
    "portal",
    "shared",
    "dashboard",
    "solar",
    "battery",
    "transmission",
    "amazon",
    "private",
    "seven",
    "blueprint",
    "pipeline",
    "governance",
    "cloud",
)

DEFAULT_WORKSTREAM_DEDUP_ANCHORS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("solar_and_battery_jarvis", ("solar and battery", "project jarvis", "jarvis", "seven screens")),
    ("infrastructure_automation", ("infrastructure automation", "remaining team profiles")),
    ("transmission_command_center", ("transmission", "command center", "fire and smoke")),
    ("cgi_grid_360", ("cgi grid 360", "grid 360", "power delivery")),
    ("shared_data_hub", ("shared google data hub", "sap and salesforce", "16-week backend sap")),
    ("company_wide_governance", ("company-wide governance", "governance layer")),
    ("signed_contract_alternative", ("alternative to a signed contract", "signed contract", "march cutoff", "funding before march", "march funding")),
)

WHITELISTED_DOMAIN_PAIRS = frozenset(
    {
        ("ai", "platform"),
        ("cloud", "environment"),
        ("employee", "portal"),
        ("employee", "records"),
        ("google", "cloud"),
        ("hr", "scope"),
        ("partner", "funding"),
        ("workstreams", "link"),
    }
)

STOPWORDS_FOR_DEDUP = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "before", "by", "for", "from",
        "if", "in", "into", "is", "it", "of", "on", "or", "so", "that", "the",
        "their", "to", "up", "with", "where", "what", "why", "first", "next",
        "week", "google", "partner_advisory", "apex_global", "team", "project", "workstreams",
        "completed", "oct", "amit", "ayer", "dan", "okray", "assistant", "assistants",
        "hr", "data", "privacy", "moveworks", "portal", "employee",
    }
)


def detect_cross_bullet_repetition(agenda_body: str, ledger: Dict[str, Any] | None = None) -> List[str]:
    """Detect repeated workstreams or duplicate multi-word phrases across sub-bullets without blocking natural domain vocabulary."""
    subbullet_texts: List[str] = []
    for ln in agenda_body.splitlines():
        match = SUBBULLET_LINE_RE.match(ln)
        if match:
            cleaned = COMPLETED_MARKER_RE.sub("", match.group(1)).strip()
            body_without_owners = re.sub(r"\([^)]*\)\.?$", "", cleaned).strip().lower()
            subbullet_texts.append(body_without_owners)

    anchors: List[Tuple[str, Tuple[str, ...]]] = list(DEFAULT_WORKSTREAM_DEDUP_ANCHORS)
    if ledger and isinstance(ledger.get("workstream_dedup_anchors"), dict):
        anchors = [(k, tuple(v)) for k, v in ledger["workstream_dedup_anchors"].items()]

    repeated: List[str] = []
    for label, phrases in anchors:
        matching_bullets = [
            idx + 1 for idx, text in enumerate(subbullet_texts) if any(p.lower() in text for p in phrases)
        ]
        if len(matching_bullets) > 1:
            repeated.append(f"{label} (in sub-bullets {matching_bullets})")

    # Only flag verbatim 4-token non-stopword spans repeated across sub-bullets so natural 2-word
    # business phrases never force artificial synonym gymnastics at the expense of accuracy.
    quadgram_to_bullets: Dict[Tuple[str, str, str, str], List[int]] = {}
    for idx, text in enumerate(subbullet_texts):
        tokens = [
            tok
            for tok in re.findall(r"[a-z0-9]+", text)
            if len(tok) > 2 and tok not in STOPWORDS_FOR_DEDUP
        ]
        seen_in_bullet = set()
        for i in range(len(tokens) - 3):
            quad = (tokens[i], tokens[i + 1], tokens[i + 2], tokens[i + 3])
            if quad not in seen_in_bullet:
                seen_in_bullet.add(quad)
                quadgram_to_bullets.setdefault(quad, []).append(idx + 1)

    for qg, bullets in quadgram_to_bullets.items():
        if len(bullets) > 1:
            phrase = " ".join(qg)
            if not any(qg[0] in r and qg[1] in r for r in repeated):
                repeated.append(f"'{phrase}' (in sub-bullets {bullets})")

    return repeated


def split_agenda_and_lineage(text: str) -> Tuple[str, str]:
    """Split agenda markdown into (agenda_body, lineage_footer)."""
    match = LINEAGE_HEADER_RE.search(text)
    if not match:
        return text.strip(), ""
    return text[: match.start()].strip(), text[match.start() :].strip()


def count_agenda_words(agenda_body: str) -> int:
    """Count whitespace-delimited words in the agenda body."""
    return len(agenda_body.split())


def load_cadence_ledger(ledger_path: Path) -> Dict[str, Any]:
    """Load the stateful weekly sync cadence ledger and merge out-of-tree mirror if present."""
    if not ledger_path.exists():
        raise FileNotFoundError(f"Cadence ledger not found at: {ledger_path}")
    primary = json.loads(ledger_path.read_text(encoding="utf-8"))
    mirror_path = resolve_mirror_ledger_path(ledger_path)
    if mirror_path.is_file():
        try:
            mirror = json.loads(mirror_path.read_text(encoding="utf-8"))
            return merge_cadence_ledgers(primary, mirror)
        except Exception:
            return primary
    return primary


def resolve_cheap_judge_tier(repo_root: Path | None = None) -> Dict[str, Any]:
    """Resolve the governed cheap LLM judge tier from config/runtime/model_registry.json."""
    root = repo_root or Path(__file__).resolve().parents[1]
    reg_path = root / MODEL_REGISTRY_REL
    if reg_path.is_file():
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        tiers = data.get("model_tiers", {})
        if "FAST_ROUTING" in tiers:
            return {"tier": "FAST_ROUTING", **tiers["FAST_ROUTING"]}
    return {"tier": "FAST_ROUTING", "reasoning_effort": "low", "timeout": 15.0}


def evaluate_layman_linguistics_gate(
    agenda_body: str,
    min_score: int = 85,
    ledger: Dict[str, Any] | None = None,
) -> Tuple[bool, int, Dict[str, Any], List[str]]:
    """Evaluate layman readability, per-sub-bullet specificity, and non-redundancy (pass >= 85/100)."""
    lower_body = agenda_body.lower()
    detected_jargon = [tok for tok in FORBIDDEN_LAYMAN_JARGON_TOKENS if tok in lower_body]
    repeated_topics = detect_cross_bullet_repetition(agenda_body, ledger=ledger)
    min_subbullet_words = int((ledger or {}).get("learned_operator_constraints", {}).get("min_subbullet_words", 12))
    specificity_errors = check_subbullet_specificity(agenda_body, min_words=min_subbullet_words)
    business_cues_found = sum(1 for cue in PLAIN_BUSINESS_OUTCOME_CUES if cue in lower_body)

    words = agenda_body.split()
    avg_word_len = sum(len(w.strip(".,;:()[]{}")) for w in words) / max(1, len(words))

    jargon_score = max(0, 100 - (len(detected_jargon) * 20))
    base_clarity = min(100, 52 + (business_cues_found * 5))
    clarity_score = max(0, base_clarity - (len(specificity_errors) * 25))
    non_redundancy_score = max(0, 100 - (len(repeated_topics) * 45))
    flow_score = 95 if 4.0 <= avg_word_len <= 5.8 else max(45, int(95 - abs(avg_word_len - 5.0) * 25))

    composite = int(
        round(
            0.35 * jargon_score
            + 0.25 * clarity_score
            + 0.25 * non_redundancy_score
            + 0.15 * flow_score
        )
    )
    tier_info = resolve_cheap_judge_tier()

    errors: List[str] = []
    if detected_jargon:
        errors.append(
            f"LAYMAN_JARGON_DETECTED: Replace unexplained technical/insider terms with plain English: {detected_jargon}"
        )
    if repeated_topics:
        errors.append(
            f"CROSS_BULLET_REPETITION_DETECTED: Consolidate repeated workstreams/phrases across sub-bullets: {repeated_topics}"
        )
    errors.extend(specificity_errors)

    # Stage B: Only invoke the live LLM semantic judge (via FAST_ROUTING) if Stage A passed
    stage_b_verdict: Dict[str, Any] = {"invoked": False, "mode": "skipped_due_to_stage_a_failure"}
    if not errors and composite >= min_score:
        stage_b_verdict = evaluate_stage_b_llm_semantic_judge(
            agenda_body=agenda_body,
            ledger=ledger,
            min_score=min_score,
            allow_live_llm=True,
        )
        if stage_b_verdict.get("invoked"):
            composite = int(round(0.50 * composite + 0.50 * int(stage_b_verdict.get("composite_score", composite))))
        for defect in stage_b_verdict.get("defects", []):
            errors.append(f"LLM_JUDGE_SEMANTIC_DEFECT: {defect}")

    if composite < min_score:
        errors.append(
            f"LAYMAN_JUDGE_SCORE_BELOW_THRESHOLD: Layman readability & non-redundancy score is {composite}/100 (minimum required is {min_score}/100)."
        )

    report = {
        "judge_tier": tier_info.get("tier", "FAST_ROUTING"),
        "judge_model": stage_b_verdict.get("model", tier_info.get("default_model", "FAST_ROUTING")),
        "stage_b_llm_verdict": stage_b_verdict,
        "composite_score": composite,
        "min_required_score": min_score,
        "subscores": {
            "zero_unexplained_jargon": jargon_score,
            "layman_business_clarity": clarity_score,
            "non_redundancy": non_redundancy_score,
            "conversational_flow": flow_score,
        },
        "detected_jargon": detected_jargon,
        "repeated_topics": repeated_topics,
        "specificity_errors": specificity_errors,
        "passed": len(errors) == 0,
    }
    return len(errors) == 0, composite, report, errors


def record_operator_feedback(
    ledger_path: Path,
    feedback_text: str,
    applied_rules: List[str] | None = None,
    max_words: int | None = None,
    retracted_claims: List[str] | None = None,
    complete_action_id: str | None = None,
    completed_by: str = "Amit Ayer",
    completed_date: str | None = None,
) -> Dict[str, Any]:
    """Persist operator feedback, retracted claims, and completed actions into JSON, mirror, and SQLite."""
    ledger = load_cadence_ledger(ledger_path)
    constraints = ledger.setdefault("learned_operator_constraints", {})
    if max_words is not None:
        constraints["max_agenda_words"] = int(max_words)
    if retracted_claims:
        quar = ledger.setdefault("retracted_or_specious_claims_quarantine", [])
        for claim in retracted_claims:
            if claim and claim not in quar:
                quar.append(claim)
    eff_date = completed_date or datetime.date.today().isoformat()
    if complete_action_id:
        for item in ledger.get("open_action_items", []):
            if item.get("action_id") == complete_action_id:
                item["status"] = "COMPLETED"
                item["completed_date"] = eff_date
                item["completed_by"] = completed_by

    history = constraints.setdefault("feedback_history", [])
    rules = applied_rules or ["OPERATOR_FEEDBACK_RULE"]
    history.append(
        {
            "recorded_at": eff_date,
            "operator_feedback": feedback_text.strip(),
            "applied_rules": rules,
        }
    )
    write_ledger_with_mirror(ledger_path, ledger)
    sync_ledger_with_active_graph(ledger, writeback=True)
    persist_system_learning_event(
        ledger_path=ledger_path,
        event_kind="OPERATOR_CADENCE_FEEDBACK",
        description=feedback_text.strip(),
        metadata={
            "applied_rules": rules,
            "max_words": constraints.get("max_agenda_words"),
            "retracted_claims": retracted_claims or [],
            "complete_action_id": complete_action_id,
        },
    )
    return ledger


def _count_bullet_structure(agenda_body: str) -> Tuple[int, int, int, int, int]:
    """Return (top_bullets, sub_bullets, non_bullet_prose_lines, leading_owner_lines, trailing_owner_lines)."""
    top_bullets = 0
    sub_bullets = 0
    non_bullet_prose_lines = 0
    leading_owner_lines = 0
    trailing_owner_lines = 0
    for ln in (line for line in agenda_body.splitlines() if line.strip()):
        stripped = ln.strip()
        if stripped.startswith("#") or stripped.startswith("**Meeting:") or stripped.startswith("---"):
            continue
        if SUBBULLET_LINE_RE.match(ln):
            sub_bullets += 1
            if LEADING_OWNER_RE.match(ln):
                leading_owner_lines += 1
            cleaned_for_owner = COMPLETED_MARKER_RE.sub("", stripped).strip()
            if TRAILING_OWNER_RE.search(cleaned_for_owner):
                trailing_owner_lines += 1
        elif BULLET_LINE_RE.match(ln):
            top_bullets += 1
        else:
            non_bullet_prose_lines += 1
    return top_bullets, sub_bullets, non_bullet_prose_lines, leading_owner_lines, trailing_owner_lines


def _check_continuity_and_insights(
    agenda_body: str,
    lower_body: str,
    constraints: Dict[str, Any],
    leading_owner_lines: int,
    trailing_owner_lines: int,
    ledger: Dict[str, Any] | None = None,
) -> List[str]:
    """Check prior action item continuity, trailing owner placement, and partner strategic insights."""
    errors: List[str] = []
    if constraints.get("require_prior_agenda_followup", True):
        owners = sum(1 for tok in ("alex", "rusty", "robson", "craig", "dan", "amit", "anya") if tok in lower_body)
        has_kw = any(k in lower_body for k in ("prior", "follow-up", "follow up", "action", "tracker", "last week"))
        if not has_kw or owners < 3:
            errors.append(
                "MISSING_PRIOR_ACTION_CONTINUITY: Agenda must include a prior-sync follow-up / action tracker referencing Google and PartnerAdvisory owners."
            )
        if ledger:
            errors.extend(check_open_and_completed_action_items(agenda_body, ledger))
    if constraints.get("require_trailing_owner_format", True):
        if leading_owner_lines > 0 or trailing_owner_lines < 3:
            errors.append(
                "TRAILING_OWNER_FORMAT_REQUIRED: Place Google/PartnerAdvisory owners at the end of each point in parentheses (never prefix sub-bullets with 'Owner:')."
            )
    if constraints.get("require_partner_strategic_agent_weighting", True):
        if ledger and ledger.get("partner_strategic_directives"):
            errors.extend(check_partner_directives_with_decay(lower_body, ledger))
        else:
            if "alex" not in lower_body:
                errors.append("MISSING_ALEX_STRATEGIC_INSIGHT: Agenda must weight Alex Brannan's guidance.")
            if "rusty" not in lower_body:
                errors.append("MISSING_RUSTY_STRATEGIC_INSIGHT: Agenda must weight Rusty Gattmann's guidance.")
    return errors


def _check_quarantine_and_confidentiality(full_text: str, lower_body: str, ledger: Dict[str, Any]) -> List[str]:
    """Check offboarded personnel quarantine, non-partner exclusion, retracted claims, and pricing leaks."""
    errors: List[str] = []
    for entry in ledger.get("offboarded_personnel_quarantine", []):
        names = [entry.get("canonical_name", "")] + list(entry.get("aliases", []))
        for qname in (n for n in names if n):
            pat = re.compile(rf"\b{re.escape(qname.lower())}\b", re.IGNORECASE)
            if pat.search(lower_body):
                errors.append(
                    f"QUARANTINED_OFFBOARDED_PERSONNEL_DETECTED: Agenda body references offboarded personnel '{qname}'."
                )
                break

    client_tokens = set(FORBIDDEN_CLIENT_AND_CROSS_MEETING_TOKENS)
    for tok in ledger.get("client_and_cross_meeting_exclusion_quarantine", []):
        client_tokens.add(str(tok).lower())
    for token in sorted(client_tokens):
        if token and token in lower_body:
            errors.append(
                f"CLIENT_ATTENDEE_OR_CROSS_MEETING_LEAK: Agenda body contains Apex Global client or cross-meeting monthly enablement item '{token}'."
            )

    for bad_term in ("18-month loss leader", "loss leader", "partner_advisorypod vs", "staff augmentation vs"):
        if bad_term in full_text.lower():
            errors.append(f"INTERNAL_CONFIDENTIALITY_LEAK: Agenda contains internal-only PartnerAdvisory strategy term '{bad_term}'.")

    for label, price_re in COMMERCIAL_PRICING_PATTERNS:
        match = price_re.search(full_text)
        if match:
            errors.append(
                f"COMMERCIAL_PRICING_LEAK_DETECTED: Agenda contains prohibited {label} ('{match.group(0)}')."
            )

    detected_specious: List[str] = []
    for label, spec_re in SPECIOUS_CLAIM_PATTERNS:
        if spec_re.search(full_text):
            detected_specious.append(label)
    for phrase in ledger.get("retracted_or_specious_claims_quarantine", []):
        if phrase and phrase.lower() in full_text.lower() and phrase not in detected_specious:
            detected_specious.append(phrase)
    if detected_specious:
        errors.append(
            f"RETRACTED_OR_SPECIOUS_CLAIM_DETECTED: Agenda contains retracted/specious claim(s) quarantined by operator & graph: {detected_specious}"
        )
    return errors


def validate_agenda_text(text: str, ledger: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Validate an agenda markdown payload against learned constraints, layman clarity, and cadence continuity."""
    errors: List[str] = []
    agenda_body, lineage_footer = split_agenda_and_lineage(text)
    constraints = ledger.get("learned_operator_constraints", {})
    max_words = int(constraints.get("max_agenda_words", 800))
    min_layman_score = int(constraints.get("min_layman_readability_score", 85))

    word_count = count_agenda_words(agenda_body)
    if word_count > max_words:
        errors.append(f"AGENDA_WORD_LIMIT_EXCEEDED: Agenda body is {word_count} words (max allowed is {max_words} words).")
    if word_count == 0:
        errors.append("EMPTY_AGENDA_BODY: Agenda body cannot be empty.")

    top_bullets, sub_bullets, prose_lines, leading_owners, trailing_owners = _count_bullet_structure(agenda_body)
    if top_bullets == 0 or sub_bullets == 0:
        errors.append(
            f"BULLET_SUBBULLET_STRUCTURE_REQUIRED: Found {top_bullets} top-level bullets and {sub_bullets} sub-bullets (both must be >= 1)."
        )
    if prose_lines > 0:
        errors.append(f"PROSE_PARAGRAPH_DETECTED: Found {prose_lines} non-bullet prose line(s); agenda body must use bullets and sub-bullets only.")

    lower_body = agenda_body.lower()
    errors.extend(_check_continuity_and_insights(agenda_body, lower_body, constraints, leading_owners, trailing_owners, ledger=ledger))
    errors.extend(_check_quarantine_and_confidentiality(text, lower_body, ledger))

    _, layman_score, layman_report, layman_errors = evaluate_layman_linguistics_gate(
        agenda_body, min_score=min_layman_score, ledger=ledger
    )
    if constraints.get("require_layman_readability_gate", True):
        errors.extend(layman_errors)

    graph_sync = sync_ledger_with_active_graph(ledger)
    directive_weights = {
        d.get("directive_id", f"dir-{i}"): compute_directive_effective_weight(d)
        for i, d in enumerate(ledger.get("partner_strategic_directives", []))
    }

    metrics = {
        "agenda_word_count": word_count,
        "max_agenda_words": max_words,
        "top_bullets": top_bullets,
        "sub_bullets": sub_bullets,
        "trailing_owner_subbullets": trailing_owners,
        "has_lineage_footer": bool(lineage_footer),
        "layman_readability_score": layman_score,
        "layman_judge_report": layman_report,
        "directive_effective_weights": directive_weights,
        "graph_sync": graph_sync,
    }
    return len(errors) == 0, errors, metrics


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Google x PartnerAdvisory Weekly Sync agenda and record feedback.")
    parser.add_argument("--file", type=Path, help="Path to markdown agenda file to validate.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_REL, help="Path to cadence ledger JSON.")
    parser.add_argument("--record-feedback", type=str, help="Record new operator feedback into the cadence ledger.")
    parser.add_argument("--max-words", type=int, help="Optional updated max word cap when recording feedback.")
    parser.add_argument("--retracted-claim", action="append", default=[], help="Quarantine a retracted or specious claim.")
    parser.add_argument("--complete-action", type=str, help="Mark an action_id (e.g. ACT-20261027-02) as COMPLETED.")
    parser.add_argument("--completed-by", type=str, default="Amit Ayer", help="Owner who completed the action item.")
    parser.add_argument("--applied-rule", action="append", default=[], help="Rule identifier(s) applied with feedback.")
    args = parser.parse_args(argv)

    if args.record_feedback or args.retracted_claim or args.complete_action:
        record_operator_feedback(
            args.ledger,
            args.record_feedback or "Updated cadence ledger state via CLI.",
            applied_rules=args.applied_rule or None,
            max_words=args.max_words,
            retracted_claims=args.retracted_claim,
            complete_action_id=args.complete_action,
            completed_by=args.completed_by,
        )
        print(f"[LEARNED] Recorded operator feedback and synced persistent store for {args.ledger}")

    if args.file:
        ledger = load_cadence_ledger(args.ledger)
        text = args.file.read_text(encoding="utf-8")
        ok, errors, metrics = validate_agenda_text(text, ledger)
        if not ok:
            print(f"[FAIL] {args.file.name} failed Google x PartnerAdvisory cadence validation:")
            for err in errors:
                print(f"  - {err}")
            return 1
        print(
            f"[PASS] {args.file.name} passed cadence & layman validation ({metrics['agenda_word_count']}/{metrics['max_agenda_words']} words, "
            f"layman score {metrics['layman_readability_score']}/100, {metrics['top_bullets']} bullets, {metrics['sub_bullets']} sub-bullets)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
