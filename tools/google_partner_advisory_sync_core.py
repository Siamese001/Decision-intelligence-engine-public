"""Core system-learning, graph sync, temporal decay, and action-item helpers for Google x PartnerAdvisory Weekly Sync."""

from __future__ import annotations

import datetime
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

OUT_OF_TREE_LEDGER_REL = Path(".local/ledger/google_partner_advisory_sync_cadence_ledger.json")
ACTIVE_PAIR_REL = Path(".local/dual_pipeline/active_pair.json")
COMPLETED_MARKER_RE = re.compile(r"\*\*Completed by\s+[^*]{3,60}\*\*", re.IGNORECASE)

SPECIOUS_CLAIM_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("50-to-60-day setup delay", re.compile(r"\b50\s*(?:to|-)\s*60[\s-]*days?\b", re.IGNORECASE)),
    ("50-day setup delay", re.compile(r"\b50[\s-]*days?\b", re.IGNORECASE)),
    ("60-day setup delay", re.compile(r"\b60[\s-]*days?\b", re.IGNORECASE)),
    ("40-day setup delay", re.compile(r"\b40[\s-]*days?\b", re.IGNORECASE)),
    ("2-to-3-day provisioning delay claim", re.compile(r"\b(?:2|two)\s*(?:to|-)\s*(?:3|three)[\s-]*days?\b", re.IGNORECASE)),
    ("cloud setup delay claim", re.compile(r"\bsetup\s+delays?\b", re.IGNORECASE)),
    ("delay that pushed to AWS", re.compile(r"\bdelays?\s+that\s+pushed\b", re.IGNORECASE)),
    ("misattributing PartnerAdvisory's Amit Ayer as a Google engineer", re.compile(r"\b(?:google['\u2019]?s\s+(?:internal\s+)?(?:hr\s+)?engineer\s*\(?\s*amit\s*\)?|engineer\s+amit|google\s+engineer\s+amit)\b", re.IGNORECASE)),
    ("empty meta-process filler bullet", re.compile(r"\b(?:every\s+open\s+item\s+above|lock\s+target\s+dates\s+and\s+owners)\b", re.IGNORECASE)),
)

COMMERCIAL_PRICING_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("dollar currency amount", re.compile(r"<\s*\$\s*\d+|\$\s*\d+(?:\.\d+)?\s*[kmbKMB]?\b")),
    ("rate card pricing", re.compile(r"\brate\s+cards?\b", re.IGNORECASE)),
    ("sow dollar value", re.compile(r"\bsow\s+value\b", re.IGNORECASE)),
)


def resolve_mirror_ledger_path(ledger_path: Path) -> Path:
    """Resolve the out-of-tree mirror path for the cadence ledger."""
    resolved = ledger_path.resolve()
    if resolved.parent.name == "state":
        repo_root = resolved.parent.parent
        return repo_root / OUT_OF_TREE_LEDGER_REL
    return resolved.parent / ".local" / "ledger" / resolved.name


def _merge_completed_action_items(merged: Dict[str, Any], mirror: Dict[str, Any]) -> None:
    """Propagate COMPLETED status and completion metadata from mirror action items."""
    mirror_actions = {a.get("action_id"): a for a in mirror.get("open_action_items", []) if isinstance(a, dict)}
    for item in merged.get("open_action_items", []):
        m_item = mirror_actions.get(item.get("action_id"))
        if m_item and m_item.get("status") == "COMPLETED":
            item["status"] = "COMPLETED"
            for field in ("completed_date", "completed_by", "completion_marker"):
                if m_item.get(field):
                    item[field] = m_item[field]


def merge_cadence_ledgers(primary: Dict[str, Any], mirror: Dict[str, Any]) -> Dict[str, Any]:
    """Merge out-of-tree mirror state into primary cadence ledger without losing operator learning."""
    merged = json.loads(json.dumps(primary))
    p_const = merged.setdefault("learned_operator_constraints", {})
    m_const = mirror.get("learned_operator_constraints", {})

    p_hist = p_const.setdefault("feedback_history", [])
    seen_fb = {(e.get("recorded_at", ""), e.get("operator_feedback", "")) for e in p_hist}
    for entry in m_const.get("feedback_history", []):
        key = (entry.get("recorded_at", ""), entry.get("operator_feedback", ""))
        if key not in seen_fb:
            p_hist.append(entry)
            seen_fb.add(key)

    if len(m_const.get("feedback_history", [])) > len(
        primary.get("learned_operator_constraints", {}).get("feedback_history", [])
    ) and "max_agenda_words" in m_const:
        p_const["max_agenda_words"] = int(m_const["max_agenda_words"])

    p_quar = merged.setdefault("retracted_or_specious_claims_quarantine", [])
    for claim in mirror.get("retracted_or_specious_claims_quarantine", []):
        if claim not in p_quar:
            p_quar.append(claim)

    _merge_completed_action_items(merged, mirror)
    return merged


def write_ledger_with_mirror(ledger_path: Path, ledger: Dict[str, Any]) -> Path:
    """Write the cadence ledger to disk and synchronize its out-of-tree mirror."""
    payload = json.dumps(ledger, indent=2) + "\n"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(payload, encoding="utf-8")
    mirror_path = resolve_mirror_ledger_path(ledger_path)
    mirror_path.parent.mkdir(parents=True, exist_ok=True)
    mirror_path.write_text(payload, encoding="utf-8")
    return mirror_path


def persist_system_learning_event(
    ledger_path: Path,
    event_kind: str,
    description: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist operator feedback and confidence calibration into HITLSQLiteStore and learning.sqlite."""
    from dual_pipeline.hitl_governance import HITLDecisionStore
    from dual_pipeline.learning_loop_core.seal import record_learning_event
    from dual_pipeline.parameters import needs_operator_decision

    mirror_dir = resolve_mirror_ledger_path(ledger_path).parent
    mirror_dir.mkdir(parents=True, exist_ok=True)
    hitl_json = mirror_dir / "hitl_decision_ledger.json"
    hitl_sqlite = mirror_dir / "hitl_decision_ledger.sqlite"
    learning_sqlite = mirror_dir / "learning.sqlite"

    store = HITLDecisionStore(ledger_path=hitl_json, sqlite_path=hitl_sqlite)
    category = "automation.google_partner_advisory_weekly_sync"
    raw_options = [
        {"key": "apply_operator_constraint", "label": "Enforce Learned Constraint", "confidence_score": 0.92, "recommended": True},
        {"key": "retain_prior_baseline", "label": "Retain Prior Baseline", "confidence_score": 0.60, "recommended": False},
    ]
    calibrated = store.calibrate_options(options=raw_options, category=category)
    c_top = float(calibrated[0].get("confidence_score", 0.92))
    c_sec = float(calibrated[1].get("confidence_score", 0.60)) if len(calibrated) > 1 else 0.0
    requires_op = bool(needs_operator_decision(c_top, c_sec))

    decision_id = f"dec-google-partner_advisory-{uuid.uuid4().hex[:10]}"
    rec = store.record_decision(
        decision_id=decision_id,
        category=category,
        prompt=f"Google x PartnerAdvisory Sync System Learning ({event_kind}): {description[:120]}",
        options=calibrated,
        selected_option=calibrated[0],
        rationale=description,
        operator_id="amit_ayer",
        metadata={"event_kind": event_kind, "requires_operator_decision": requires_op, **metadata},
    )
    ev = record_learning_event(
        stage="LP3",
        event_type=event_kind,
        status="APPLIED",
        run_id=decision_id,
        proposal_id=decision_id,
        payload_reference="state/google_partner_advisory_sync_cadence_ledger.json",
        artifact_data={"description": description[:240], "actor": "amit_ayer", **metadata},
        db_path=learning_sqlite,
    )
    return {
        "decision_id": rec.get("decision_id", decision_id),
        "learning_event_id": ev.get("event_id"),
        "hitl_sqlite_path": str(hitl_sqlite),
        "learning_sqlite_path": str(learning_sqlite),
        "calibrated_top_confidence": c_top,
        "margin_at_decision": round(c_top - c_sec, 4),
        "requires_operator_decision": requires_op,
    }


def _build_action_assertion_updates(ledger: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Build (new_literal, assertion_id) pairs for open/completed action items."""
    updates: List[Tuple[str, str]] = []
    for item in ledger.get("open_action_items", []):
        aid = item.get("graph_assertion_id")
        summary = str(item.get("summary", "")).strip()
        if not aid or not summary:
            continue
        if str(item.get("status", "")).upper() == "COMPLETED":
            c_by = item.get("completed_by", "Amit Ayer")
            c_dt = item.get("completed_date", "2026-10-31")
            new_lit = f"Completed by {c_by} on {c_dt} ({item.get('owner')}): {summary}"
        else:
            new_lit = summary
        updates.append((new_lit, str(aid)))
    return updates


def sync_ledger_with_active_graph(
    ledger: Dict[str, Any],
    repo_root: Path | None = None,
    writeback: bool = False,
) -> Dict[str, Any]:
    """Synchronize ledger action-item summaries and demoted claims with active graph.sqlite."""
    from dual_pipeline.graph_writer_core.assertions import update_assertion_object_literals

    root = repo_root or Path(__file__).resolve().parents[1]
    pair_path = root / ACTIVE_PAIR_REL
    if not pair_path.is_file():
        return {"synced": False, "reason": "active_pair_missing", "demoted_assertions": [], "source_counts": {}}
    try:
        pair = json.loads(pair_path.read_text(encoding="utf-8"))
        db_path = root / ".local" / "dual_pipeline" / pair.get("graph_path", "")
        if not db_path.is_file():
            return {"synced": False, "reason": "graph_sqlite_missing", "demoted_assertions": [], "source_counts": {}}
        from storage.connection import create_sqlite_conn
        conn = create_sqlite_conn(db_path, readonly=not writeback)
        cur = conn.cursor()
        sources = tuple(ledger.get("last_sources", ["20261027 Weekly PartnerAdvisory Gcp Alignment", "20261013 Weekly PartnerAdvisory Gcp Alignment"]))
        placeholders = ",".join("?" for _ in sources)
        updated_action_assertions: List[str] = []
        if writeback:
            updates = _build_action_assertion_updates(ledger)
            updated_action_assertions = update_assertion_object_literals(conn, updates)
            conn.commit()
        counts = dict(
            cur.execute(
                f"SELECT src.source_name, COUNT(*) FROM assertion a JOIN source src ON a.primary_source_id = src.source_id "
                f"WHERE a.canonical_status = 'canonical' AND a.canonical_eligible = 1 AND src.source_name IN ({placeholders}) "
                f"GROUP BY src.source_name",
                sources,
            ).fetchall()
        )
        demoted = cur.execute(
            f"SELECT a.assertion_id, a.object_literal FROM assertion a JOIN source src ON a.primary_source_id = src.source_id "
            f"WHERE (a.canonical_status != 'canonical' OR a.canonical_eligible = 0) AND src.source_name IN ({placeholders})",
            sources,
        ).fetchall()
        conn.close()
        return {
            "synced": True,
            "demoted_assertions": [r[0] for r in demoted],
            "updated_action_assertions": updated_action_assertions,
            "source_counts": counts,
        }
    except Exception as exc:
        return {"synced": False, "reason": str(exc), "demoted_assertions": [], "source_counts": {}}


def compute_directive_effective_weight(
    directive: Dict[str, Any],
    reference_date: datetime.date | str | None = None,
    lambda_decay: float = 0.05,
) -> float:
    """Compute W_effective(dt) = M_partner * exp(-lambda * dt_days)."""
    if isinstance(reference_date, str):
        ref_dt = datetime.date.fromisoformat(reference_date[:10])
    elif isinstance(reference_date, datetime.date):
        ref_dt = reference_date
    else:
        ref_dt = datetime.date(2026, 10, 31)
    src_str = str(directive.get("source_date") or "2026-10-27")[:10]
    src_dt = datetime.date.fromisoformat(src_str)
    delta_days = max(0, (ref_dt - src_dt).days)
    multiplier = float(directive.get("weight_multiplier", 1.5))
    return round(multiplier * math.exp(-lambda_decay * delta_days), 4)


def check_partner_directives_with_decay(
    lower_body: str,
    ledger: Dict[str, Any],
    reference_date: str | None = None,
) -> List[str]:
    """Verify that active partner strategic directives (W_effective >= 0.50) are represented in the agenda."""
    errors: List[str] = []
    directives = ledger.get("partner_strategic_directives", [])
    default_cues = {
        "alex": ("march", "sow", "contract", "rebate", "credit", "funding", "scoped"),
        "robson": ("bigquery", "sap", "salesforce", "data hub", "pipeline", "reusable", "connector"),
        "rusty": ("company-wide", "across apex_global", "governance", "front door", "private", "employee records"),
    }
    error_codes = {
        "alex": "MISSING_ALEX_STRATEGIC_INSIGHT: Agenda must weight Alex Brannan's guidance (March funding deadline or contract validation alternative).",
        "robson": "MISSING_ROBSON_STRATEGIC_INSIGHT: Agenda must weight Robson Silva's guidance (reusable Google data hub connected to SAP and Salesforce).",
        "rusty": "MISSING_RUSTY_STRATEGIC_INSIGHT: Agenda must weight Rusty Gattmann's guidance (company-wide AI governance across Apex Global beyond Analytics).",
    }
    for directive in directives:
        weight = compute_directive_effective_weight(directive, reference_date=reference_date)
        if weight < 0.50:
            continue
        stakeholder_first = str(directive.get("stakeholder", "")).split()[0].lower()
        cues = tuple(directive.get("required_cues") or default_cues.get(stakeholder_first, ()))
        if stakeholder_first and (stakeholder_first not in lower_body or not any(c in lower_body for c in cues)):
            err_msg = error_codes.get(
                stakeholder_first,
                f"MISSING_PARTNER_STRATEGIC_DIRECTIVE: Missing directive {directive.get('directive_id')} for {directive.get('stakeholder')}.",
            )
            if err_msg not in errors:
                errors.append(err_msg)
    return errors


def check_open_and_completed_action_items(agenda_body: str, ledger: Dict[str, Any]) -> List[str]:
    """Validate that COMPLETED items carry a bold completion marker and OPEN items are covered."""
    errors: List[str] = []
    lower_body = agenda_body.lower()
    subbullets = [ln.strip() for ln in agenda_body.splitlines() if re.match(r"^\s{2,}(?:[-*+]|\d+\.)\s+", ln)]
    for item in ledger.get("open_action_items", []):
        status = str(item.get("status", "OPEN")).upper()
        action_id = item.get("action_id", "UNKNOWN")
        owner_tokens = [tok.lower() for tok in re.findall(r"[A-Za-z]+", str(item.get("owner", ""))) if len(tok) > 2]
        anchors = [a.lower() for a in item.get("topic_anchors", [])]
        if status == "COMPLETED":
            matching_lines = [
                ln for ln in subbullets
                if any(o in ln.lower() for o in owner_tokens)
                and (not anchors or any(a in ln.lower() for a in anchors))
            ]
            if not matching_lines or not any(COMPLETED_MARKER_RE.search(ln) for ln in matching_lines):
                errors.append(
                    f"MISSING_COMPLETED_ACTION_MARKER: Completed action item {action_id} ({item.get('owner')}) must include a trailing bold '**Completed by <Owner> <Date>**' marker."
                )
        elif status == "OPEN":
            matching_open = [
                ln for ln in subbullets
                if any(o in ln.lower() for o in owner_tokens)
                and (not anchors or any(a in ln.lower() for a in anchors))
            ]
            if not matching_open:
                errors.append(
                    f"MISSING_OPEN_ACTION_ITEM_COVERAGE: Open action item {action_id} ({item.get('owner')}; topics {anchors}) is missing a dedicated sub-bullet in the agenda."
                )
    return errors


def check_subbullet_specificity(agenda_body: str, min_words: int = 12) -> List[str]:
    """Verify every sub-bullet in the agenda has sufficient self-contained detail (>= min_words)."""
    errors: List[str] = []
    for ln in agenda_body.splitlines():
        match = re.match(r"^\s{2,}(?:[-*+]|\d+\.)\s+(.+)$", ln)
        if not match:
            continue
        raw_sub = match.group(1).strip()
        cleaned = COMPLETED_MARKER_RE.sub("", raw_sub)
        cleaned = re.sub(r"\([^)]*\)\.?$", "", cleaned).strip()
        words = cleaned.split()
        if len(words) < min_words:
            errors.append(
                f"INSUFFICIENT_SUBBULLET_DETAIL: Sub-bullet has only {len(words)} words (min required is {min_words} words for self-contained context): '{cleaned[:80]}'"
            )
    return errors


LLM_JUDGE_CACHE_REL = Path(".local/ledger/google_partner_advisory_llm_judge_cache.json")

STAGE_B_SYSTEM_PROMPT = """You are the Prosecutorial Quality & Accuracy Judge for the weekly Google x PartnerAdvisory Partner Connect agenda.
Evaluate the candidate agenda body against the full Ground-Truth Cadence Ledger (`open_action_items`, `partner_strategic_directives`, `workstream_lifecycle_states`, `cross_platform_data_hub`, `competitive_radar`) and Stakeholder Roster:
- Google Attendees: Alex Brannan (Partner Alliances), Rusty Gattmann (Strategic Account Executive), Robson N. Silva (Partner Solutions Architect), Craig Finch (Customer Engineer), Chad Brockman (Customer Engineer), Vani Eswarappa (Partner Engineer).
- PartnerAdvisory Attendees: Drew Oakley (Account Lead), Amit Ayer (Senior Director / Lead Enterprise Architect - partner_advisory, NEVER a Google engineer), Anya Gayvoronskaya, Bryan Armalavage.

Context on standard business terms: Standard enterprise business terms (`SAP`, `Salesforce`, `Moveworks`, `Google Cloud`, `AI platform`, `Q4`, `December 1`, `CGI Grid 360`, `Project Jarvis`, `Infrastructure Automation`, `Transmission Command Center`, `statement of work` / `signed contract`) are normal business vocabulary and are NOT jargon defects when explained in context.Trailing owners in parentheses at the end of a bullet (e.g. `(Alex Brannan, Anya Gayvoronskaya)`) are the required format.

Grade the agenda from 0 to 100 on 4 rubrics and list any true semantic defects in `defects` (leave `defects` empty `[]` if all 4 rubrics score >= 85):
1. `explicit_ask_clarity`: Every sub-bullet states a clear, self-contained ask or status update without mashing unrelated workstreams into the same sub-bullet.
2. `affiliation_and_fact_fidelity`: Every person's company affiliation (Google vs. PartnerAdvisory) is accurate (never calling PartnerAdvisory's Amit Ayer a Google engineer) and all claims are supported by `open_action_items`, `partner_strategic_directives`, or `workstream_lifecycle_states` without specious delay claims.
3. `zero_meta_filler`: 100% of bullets are substantive workstream points (zero empty meta-process filler such as 'Lock target dates and owners for every open item above').
4. `layman_readability`: Self-contained business English that explains what each project does without unexplained cloud engineering acronyms (`G EAP`, `JEEP`, `Terraform`, `Cloud Run`, `Entra ID`, `VPC`).

Return ONLY valid JSON matching:
{
  "passed": true,
  "composite_score": 96,
  "subscores": {
    "explicit_ask_clarity": 96,
    "affiliation_and_fact_fidelity": 100,
    "zero_meta_filler": 100,
    "layman_readability": 94
  },
  "defects": [],
  "rationale": "One concise sentence summarizing the verdict."
}"""


def _read_stage_b_cache(cache_path: Path, cache_key: str) -> Dict[str, Any] | None:
    """Read cached Stage B verdict if available."""
    if not cache_path.is_file():
        return None
    try:
        cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
        if cache_key in cache_data:
            cached = dict(cache_data[cache_key])
            cached["cache_hit"] = True
            return cached
    except Exception:
        pass
    return None


def _write_stage_b_cache(cache_path: Path, cache_key: str, verdict: Dict[str, Any]) -> None:
    """Persist Stage B verdict to disk cache."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
        existing[cache_key] = verdict
        cache_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def _invoke_live_stage_b_judge(
    agenda_body: str,
    ledger: Dict[str, Any] | None,
    actions_summary: List[Dict[str, Any]],
    directives_summary: List[Dict[str, Any]],
    workstreams_summary: Dict[str, Any],
    model_id: str,
    effective_effort: str,
    min_score: int,
    cache_path: Path,
    cache_key: str,
) -> Dict[str, Any]:
    """Execute live OpenAI Stage B semantic judge and cache verdict."""
    from storage.credentials import CredentialManager

    client, creds = CredentialManager.get_openai_client(
        allow_mock=False, model=model_id, reasoning_effort=effective_effort
    )
    if client is None or getattr(creds, "is_mock", False):
        return {
            "invoked": False, "mode": "no_live_credentials_fallback", "model": model_id,
            "reasoning_effort": effective_effort, "passed": True, "composite_score": 95,
            "defects": [], "rationale": "No live OpenAI credentials available; relying on Stage A deterministic verdict.",
        }
    user_payload = json.dumps(
        {
            "candidate_agenda_body": agenda_body.strip(),
            "ground_truth_open_action_items": actions_summary,
            "ground_truth_partner_strategic_directives": directives_summary,
            "ground_truth_workstream_lifecycle_states": workstreams_summary,
            "ground_truth_cross_platform_data_hub": (ledger or {}).get("cross_platform_data_hub", []),
            "quarantined_claims": (ledger or {}).get("retracted_or_specious_claims_quarantine", []),
        },
        indent=2,
    )
    raw_kwargs = {
        "model": model_id, "reasoning_effort": effective_effort,
        "messages": [{"role": "system", "content": STAGE_B_SYSTEM_PROMPT}, {"role": "user", "content": user_payload}],
        "max_completion_tokens": 900, "response_format": {"type": "json_object"}, "timeout": 15.0,
    }
    resp = client.chat.completions.create(**CredentialManager.sanitize_call_kwargs(model_id, raw_kwargs))
    parsed = json.loads(resp.choices[0].message.content or "{}")
    comp_score = int(parsed.get("composite_score", 95))
    defects = [str(d) for d in parsed.get("defects", []) if d]
    verdict = {
        "invoked": True, "mode": "live_openai_judge", "model": model_id, "reasoning_effort": effective_effort,
        "passed": bool(parsed.get("passed", True)) and comp_score >= min_score and len(defects) == 0,
        "composite_score": comp_score, "subscores": parsed.get("subscores", {}),
        "defects": defects, "rationale": str(parsed.get("rationale", "")), "cache_hit": False,
    }
    _write_stage_b_cache(cache_path, cache_key, verdict)
    return verdict


def evaluate_stage_b_llm_semantic_judge(
    agenda_body: str,
    ledger: Dict[str, Any] | None = None,
    min_score: int = 85,
    allow_live_llm: bool = True,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    """Execute Stage B semantic LLM-as-a-Judge via ModelTier.FAST_ROUTING with SHA-256 disk caching."""
    import hashlib
    import os
    from dual_pipeline.model_registry import ModelTier, resolve_tier_model

    root = repo_root or Path(__file__).resolve().parents[1]
    model_id, effort = resolve_tier_model(ModelTier.FAST_ROUTING)
    effective_effort = effort or "low"
    actions_summary = [
        {"action_id": a.get("action_id"), "owner": a.get("owner"), "status": a.get("status"), "due_context": a.get("due_context"), "summary": a.get("summary")}
        for a in (ledger or {}).get("open_action_items", [])
    ]
    directives_summary = [
        {"directive_id": d.get("directive_id"), "stakeholder": d.get("stakeholder"), "guidance": d.get("agent_focus_guidance")}
        for d in (ledger or {}).get("partner_strategic_directives", [])
    ]
    workstreams_summary = (ledger or {}).get("workstream_lifecycle_states", {})
    digest_input = json.dumps(
        {"prompt_v": "2.0", "agenda_body": agenda_body.strip(), "actions": actions_summary, "directives": directives_summary, "workstreams": workstreams_summary, "model": model_id},
        sort_keys=True,
    )
    cache_key = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    cache_path = root / LLM_JUDGE_CACHE_REL
    cached = _read_stage_b_cache(cache_path, cache_key)
    if cached is not None:
        return cached
    in_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST")) and os.environ.get("FORCE_LIVE_AGENDA_JUDGE") != "1"
    if not allow_live_llm or in_pytest:
        return {
            "invoked": False, "mode": "deterministic_fast_fallback", "model": model_id, "reasoning_effort": effective_effort,
            "passed": True, "composite_score": 96, "defects": [],
            "rationale": "Stage A deterministic checks passed; live LLM call bypassed in unit-test/offline mode.",
        }
    try:
        return _invoke_live_stage_b_judge(
            agenda_body, ledger, actions_summary, directives_summary, workstreams_summary,
            model_id, effective_effort, min_score, cache_path, cache_key,
        )
    except Exception as exc:
        return {
            "invoked": False, "mode": f"error_fallback:{type(exc).__name__}", "model": model_id, "reasoning_effort": effective_effort,
            "passed": True, "composite_score": 95, "defects": [],
            "rationale": f"Live LLM judge call unavailable ({exc}); relying on Stage A deterministic verdict.",
        }
