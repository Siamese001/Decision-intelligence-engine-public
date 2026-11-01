"""Deterministic offline graph generator for CI, testing, and offline intake execution.

Produces contract-compliant CanonicalSignalProjectionV57 payloads with positive
graph mutations and verbatim grounded evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple


def _extract_document_directive(source: str, limit: int) -> Optional[str]:
    primary = next(
        (
            match.group().strip()
            for match in re.finditer(r"[^.!?\n]+[.!?]?", source)
            if 20 <= len(match.group().strip()) <= limit
            and re.search(r"\b(?:must|will|should|needs? to|would like to)\b", match.group(), re.I)
        ),
        None,
    )
    if primary is not None:
        return primary
    return next(
        (
            match.group().strip()
            for match in re.finditer(r"[^.!?\n]+[.!?]?", source)
            if 20 <= len(match.group().strip()) <= limit
        ),
        None,
    )


def _document_fixture(user_content: str) -> Optional[str]:
    """Provide one verbatim, directive-backed signal for offline document tests."""
    source_match = re.search(r"<document_source>\s*([\s\S]*?)\s*</document_source>", user_content)
    if not source_match:
        return None
    source = source_match.group(1)
    metadata_match = re.search(r"<document_metadata>\s*([\s\S]*?)\s*</document_metadata>", user_content)
    try:
        metadata = json.loads(metadata_match.group(1)) if metadata_match else {}
    except json.JSONDecodeError:
        metadata = {}
    from dual_pipeline.evidence_field_limits import envelope_field_limits
    limit = envelope_field_limits()["statement"]
    directive = _extract_document_directive(source, limit)
    if directive is None:
        return None
    audiences = metadata.get("audiences") or []
    if isinstance(audiences, str):
        audiences = [audience.strip() for audience in audiences.split(",") if audience.strip()]
    suffix = hashlib.sha256(directive.encode("utf-8")).hexdigest()[:12]
    evidence_id = f"ev-document-{suffix}"
    return json.dumps({
        "schema_version": "5.7",
        "meeting_id": f"document-{suffix}",
        "classification": metadata.get("classification", "internal"),
        "audiences": audiences,
        "expected_base_graph_version": "1.0.0",
        "evidence_only_statement_ids": [],
        "evidence_index": {evidence_id: {
            "statement_id": evidence_id,
            "kind": "strategic",
            "source_classification": "strategic",
            "text": directive,
            "exact_text": directive,
            "locator": {"kind": "section", "value": "p1"},
        }},
        "graph_mutations": {
            "entity_upserts": [], "relationship_signals": [], "decision_updates": [],
            "proposal_updates": [], "action_updates": [], "risk_updates": [],
            "strategic_intent_signals": [{
                "signal_id": f"strategic:document-{suffix}",
                "text": directive,
                "evidence_ids": [evidence_id],
            }],
        },
    })


def _extract_metadata(user_content_or_metadata: Any, legacy_metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(user_content_or_metadata, dict):
        return user_content_or_metadata
    if legacy_metadata:
        return legacy_metadata
    if isinstance(user_content_or_metadata, str):
        m = re.search(r"<meeting_metadata>\s*([\s\S]*?)\s*</meeting_metadata>", user_content_or_metadata)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass
    return {}


def _is_0925_sow(slug: str, date_raw: str, metadata: Dict[str, Any]) -> bool:
    slug_match = "sow" in slug or "karyn" in slug
    date_match = "09-25" in date_raw or "09/25" in str(metadata.get("date", ""))
    return slug_match and date_match


def _build_0925_sow_projection(meeting_id: str, metadata: Dict[str, Any]) -> str:
    projection = {
        "schema_version": "5.7",
            "meeting_id": meeting_id,
            "classification": metadata.get("classification", "confidential"),
            "audiences": metadata.get("audiences", ["nee-engineering", "partner_advisory-delivery"]),
            "expected_base_graph_version": "1.0.0",
            "evidence_only_statement_ids": [],
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "speaker": "Morgan Vance Krutilla",
                    "kind": "technical",
                    "source_classification": "technical",
                    "text": "The rules are going to be set. There is a whole experience angle to the UI. So Alex, I think one of the things that we are going to work with, was it Alex, Cora?",
                    "exact_text": "The rules are going to be set.",
                    "locator": {"kind": "timestamp", "value": "00:45"}
                },
                "ev-02": {
                    "statement_id": "ev-02",
                    "speaker": "Alex Sanchez",
                    "kind": "strategic",
                    "source_classification": "strategic",
                    "text": "You know, long story short, like this is a PartnerAdvisory pod. Ideally, we would have reached out to those folks and got that buy-in. It did not happen, right? So that has to be another sub-workstream. We have to have like a working session with them.",
                    "exact_text": "You know, long story short, like this is a PartnerAdvisory pod.",
                    "locator": {"kind": "timestamp", "value": "04:15"}
                },
                "ev-03": {
                    "statement_id": "ev-03",
                    "speaker": "Jen Pan",
                    "kind": "decision",
                    "source_classification": "decision",
                    "text": "Yeah, yeah. So the 200 number, how we pick them is based off of the spreadsheet that Alex Coro gave us that has the mappings of for a type of asset.",
                    "exact_text": "Yeah, yeah. So the 200 number, how we pick them is based off of the spreadsheet that Alex Coro gave us that has the mappings of for a type of asset.",
                    "locator": {"kind": "timestamp", "value": "08:45"}
                },
                "ev-04": {
                    "statement_id": "ev-04",
                    "speaker": "Jake Stark",
                    "kind": "technical",
                    "source_classification": "technical",
                    "text": "It won not with the new changes we made to the pipeline. It does not require a serial number. If there is no serial number, then we should not ask someone to try to find one, right? Okay.",
                    "exact_text": "It won't with the new changes we made to the pipeline. It doesn't require a serial number.",
                    "locator": {"kind": "timestamp", "value": "20:30"}
                },
                "ev-05": {
                    "statement_id": "ev-05",
                    "speaker": "Jake Stark",
                    "kind": "risk",
                    "source_classification": "risk",
                    "text": "We do not have access to it. That exposes us heavily, right? Would you agree? I work with her to construct it, but none of us have visibility into the actual tools. Okay. Do we feel that is a good practice?",
                    "exact_text": "We don't have access to it. That exposes us heavily, right?",
                    "locator": {"kind": "timestamp", "value": "13:45"}
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:karyn-krutilla", "type": "person", "name": "Morgan Vance Krutilla", "mutation": "CONFIRM", "evidence_ids": ["ev-01"]},
                    {"id": "person:alex-sanchez", "type": "person", "name": "Alex Sanchez", "mutation": "CONFIRM", "evidence_ids": ["ev-01", "ev-02"]},
                    {"id": "person:jake-stark", "type": "person", "name": "Jake Stark", "mutation": "CONFIRM", "evidence_ids": ["ev-04", "ev-05"]},
                    {"id": "person:jen-pan", "type": "person", "name": "Jen Pan", "mutation": "CONFIRM", "evidence_ids": ["ev-03"]},
                    {"id": "person:cora-duke-botros", "type": "person", "name": "Cora Duke-Botros", "mutation": "CREATE", "evidence_ids": ["ev-01", "ev-03"]},
                    {"id": "person:anthony-funk", "type": "person", "name": "Parker Vance", "mutation": "CREATE", "evidence_ids": ["ev-02"]},
                    {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "mutation": "CONFIRM", "evidence_ids": ["ev-02"]},
                    {"id": "org:apex_global-energy", "type": "org", "name": "Apex Global Enterprises", "mutation": "CONFIRM", "evidence_ids": ["ev-01"]},
                    {"id": "project:project-lens", "type": "project", "name": "Project LENS", "mutation": "CONFIRM", "evidence_ids": ["ev-01", "ev-04"]},
                    {"id": "workstream:rules-codification", "type": "workstream", "name": "Rules Codification Workstream", "mutation": "CREATE", "evidence_ids": ["ev-01"]}
                ],
                "relationship_signals": [
                    {"signal_id": "sig-01", "from_id": "person:karyn-krutilla", "to_id": "org:partner_advisory", "predicate": "MEMBER_OF", "relationship": "MEMBER_OF", "evidence_ids": ["ev-01"]},
                    {"signal_id": "sig-02", "from_id": "person:alex-sanchez", "to_id": "org:partner_advisory", "predicate": "MEMBER_OF", "relationship": "MEMBER_OF", "evidence_ids": ["ev-02"]},
                    {"signal_id": "sig-03", "from_id": "person:jake-stark", "to_id": "org:partner_advisory", "predicate": "MEMBER_OF", "relationship": "MEMBER_OF", "evidence_ids": ["ev-04"]},
                    {"signal_id": "sig-04", "from_id": "person:jen-pan", "to_id": "org:partner_advisory", "predicate": "MEMBER_OF", "relationship": "MEMBER_OF", "evidence_ids": ["ev-03"]},
                    {"signal_id": "sig-05", "from_id": "person:cora-duke-botros", "to_id": "org:apex_global-energy", "predicate": "MEMBER_OF", "relationship": "MEMBER_OF", "evidence_ids": ["ev-01"]},
                    {"signal_id": "sig-06", "from_id": "person:anthony-funk", "to_id": "org:apex_global-energy", "predicate": "MEMBER_OF", "relationship": "MEMBER_OF", "evidence_ids": ["ev-02"]},
                    {"signal_id": "sig-07", "from_id": "workstream:rules-codification", "to_id": "project:project-lens", "predicate": "CONTRIBUTES_TO", "relationship": "CONTRIBUTES_TO", "evidence_ids": ["ev-01"]},
                    {"signal_id": "sig-08", "from_id": "person:alex-sanchez", "to_id": "project:project-lens", "predicate": "CONTRIBUTES_TO", "relationship": "CONTRIBUTES_TO", "evidence_ids": ["ev-02"]}
                ],
                "decision_updates": [
                    {"decision_id": "decision:rules-codification", "status": "APPROVED", "rationale": "Codify rules with Cora Duke-Botros using written runbooks to stabilize asset identification.", "evidence_ids": ["ev-01"]},
                    {"decision_id": "decision:pgd-working-sessions", "status": "APPROVED", "rationale": "Schedule dedicated working sessions with Parker Vance and Ed More to explain product pipeline and avoid repetitive sprint review discussions.", "evidence_ids": ["ev-02"]},
                    {"decision_id": "decision:golden-dataset-sampling", "status": "APPROVED", "rationale": "Establish 329 assets target with 200 representative sample mapped to asset types.", "evidence_ids": ["ev-03"]}
                ],
                "proposal_updates": [],
                "action_updates": [],
                "risk_updates": [
                    {"risk_id": "risk:pgd-buyin-gap", "status": "ACTIVE", "severity": "HIGH", "description": "Lack of understanding and buy-in from PGD team risks goalpost shifting and SOW acceptance delays.", "evidence_ids": ["ev-02"]},
                    {"risk_id": "risk:planner-visibility-exposure", "status": "ACTIVE", "severity": "MEDIUM", "description": "Pod lacks visibility into Mandy internal planning tool creating reliance on informal status representations.", "evidence_ids": ["ev-05"]}
                ]
        }
    }
    return json.dumps(projection, indent=2)


def _clean_person_name(name: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", name).strip()


def _clean_org_name(org_raw: str) -> str:
    lower = org_raw.lower()
    if "apex_global" in lower or "nea" in lower:
        return "Apex Global Enterprises"
    if "partner_advisory" in lower:
        return "PartnerAdvisory"
    return "Enterprise"


def _append_unique_attendee(results: List[Tuple[str, str]], name: str, org: str) -> None:
    c_name = _clean_person_name(name)
    if not c_name:
        return
    for existing_name, _ in results:
        if existing_name.lower() == c_name.lower():
            return
    results.append((c_name, org))


def _parse_dict_attendees(att: Dict[str, Any], results: List[Tuple[str, str]]) -> None:
    for org_raw, members in att.items():
        org = _clean_org_name(str(org_raw))
        mlist = members if isinstance(members, list) else [members]
        for m in mlist:
            _append_unique_attendee(results, str(m), org)


def _parse_str_attendees(att: str, results: List[Tuple[str, str]]) -> None:
    for part in att.split(","):
        org = _clean_org_name(part)
        _append_unique_attendee(results, part, org)


def _parse_roster_list(roster: List[Any], results: List[Tuple[str, str]]) -> None:
    for entry in roster:
        if isinstance(entry, dict):
            name = entry.get("name", "")
            org = entry.get("org") or _clean_org_name(str(entry))
            _append_unique_attendee(results, str(name), str(org))


def _extract_attendees_from_content(user_content: str, results: List[Tuple[str, str]]) -> None:
    for m in re.finditer(r"\*\*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*([A-Za-z0-9\s\.\-_]+?)(?:\s*[—–-]\s*([A-Za-z0-9\s\.\-_]+))?\*\*", user_content):
        _append_unique_attendee(results, m.group(1).strip(), (m.group(2) or "Enterprise").strip())
        if len(results) >= 6:
            return
    for m in re.finditer(r"###\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*\n+\*\*([^*]+?):\*\*", user_content):
        _append_unique_attendee(results, m.group(1).strip(), "Enterprise")
        if len(results) >= 6:
            return


def _extract_roster_names_and_orgs(metadata: Dict[str, Any], user_content: Any = None) -> List[Tuple[str, str]]:
    results: List[Tuple[str, str]] = []
    att = metadata.get("attendees")
    if isinstance(att, dict):
        _parse_dict_attendees(att, results)
    elif isinstance(att, str):
        _parse_str_attendees(att, results)

    roster = metadata.get("roster")
    if isinstance(roster, list):
        _parse_roster_list(roster, results)

    if not results and isinstance(user_content, str):
        _extract_attendees_from_content(user_content, results)

    if not results:
        return [("Lead Architect", "PartnerAdvisory"), ("Delivery Lead", "Apex Global Enterprises")]
    return results


def _extract_tagged_content(raw: str) -> str:
    for tag in ("raw_notes", "notes", "raw_transcript", "forensic_transcript", "document_source", "source"):
        m = re.search(rf"<{tag}>\s*([\s\S]*?)\s*</{tag}>", raw)
        if m:
            return m.group(1).strip()
    return raw


def _extract_turn_body(m: Any) -> str:
    if len(m.groups()) >= 4 and m.group(4):
        return m.group(4).strip()
    if len(m.groups()) >= 3 and m.group(3):
        return m.group(3).strip()
    return ""


def _find_clean_sentence(body: str, content: str) -> Optional[str]:
    for s in re.split(r"(?<=[.!?\n])\s+", body):
        clean_s = s.strip()
        if 20 <= len(clean_s) <= 250 and clean_s in content:
            return clean_s
    if 15 <= len(body) <= 250 and body in content:
        return body
    return None


def _match_pattern_evidence(kind: str, pat: Any, content: str) -> Optional[Tuple[str, str, str, str]]:
    for m in pat.finditer(content):
        matched_str = m.group(0) or ""
        if "session pause" in matched_str.lower():
            continue
        if kind == "spk_ts":
            spk = (m.group(1) or "Speaker").strip()
            ts = m.group(2)
            body = (m.group(3) or "").strip()
        else:
            ts = m.group(1)
            spk = (m.group(2) or "Speaker").strip()
            body = _extract_turn_body(m)
        if not body or len(body) < 15 or "session pause" in body.lower():
            continue
        quote = _find_clean_sentence(body, content)
        if quote:
            return ("timestamp", ts, spk, quote)
    return None


def _find_timestamped_evidence(content: str) -> Optional[Tuple[str, str, str, str]]:
    patterns = [
        ("ts_org", re.compile(r"\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*([A-Za-z0-9\s\.\-_]+?)(?:\s*[—–-]\s*([A-Za-z0-9\s\.\-_]+))?\*\*\s*\n+(.+?)(?=\n\*\*\[|\n##|\Z)", re.DOTALL)),
        ("ts_hdr", re.compile(r"###\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*\n+\*\*([^*]+?):\*\*\s*(.+?)(?=\n###|\n##|\Z)", re.DOTALL)),
        ("spk_ts", re.compile(r"\*\*([^*]+?):\*\*\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+?)(?=\n\*\*|\n##|\Z)", re.DOTALL)),
        ("inline", re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?:\*\*([^*]+?)\*\*:?\s*)?([^\n]+)")),
    ]
    for kind, pat in patterns:
        match = _match_pattern_evidence(kind, pat, content)
        if match:
            return match
    return None


def _find_text_evidence(content: str) -> Optional[Tuple[str, str, str, str]]:
    for s in re.split(r"(?<=[.!?\n])\s+", content):
        clean_s = s.strip()
        if 20 <= len(clean_s) <= 250 and not clean_s.startswith(("#", "<", "{", "[", "*")):
            if clean_s in content:
                return ("document", "verbatim_source", "Speaker", clean_s)
    return None


def _extract_transcript_evidence_sample(user_content_or_metadata: Any) -> Tuple[str, str, str, str]:
    if not isinstance(user_content_or_metadata, str):
        return ("metadata", "meeting_metadata", "meeting_metadata", "Reviewing enterprise architecture scope and delivery milestones.")

    content = _extract_tagged_content(user_content_or_metadata)
    ts_ev = _find_timestamped_evidence(content)
    if ts_ev is not None:
        return ts_ev
    txt_ev = _find_text_evidence(content)
    if txt_ev is not None:
        return txt_ev
    for line in content.splitlines():
        line_str = line.strip().strip("*# ")
        if 20 <= len(line_str) <= 200 and line_str in content:
            return ("document", "verbatim_source", "Speaker", line_str)
    return ("metadata", "meeting_metadata", "meeting_metadata", "Reviewing enterprise architecture scope and delivery milestones.")


def _build_generic_meeting_projection(
    meeting_id: str,
    metadata: Dict[str, Any],
    user_content: Any = None,
) -> str:
    attendees = _extract_roster_names_and_orgs(metadata, user_content)
    loc_kind, loc_val, spk, quote = _extract_transcript_evidence_sample(user_content)

    evidence_id = "ev-01"
    raw_content = _extract_tagged_content(user_content) if isinstance(user_content, str) else ""
    idx = raw_content.find(quote) if raw_content else -1
    evidence_index = {
        evidence_id: {
            "statement_id": evidence_id,
            "speaker": spk,
            "kind": "strategic",
            "source_classification": "strategic",
            "text": quote,
            "exact_text": quote,
            "locator": {"kind": loc_kind, "value": loc_val},
            "grounding_status": "GROUNDED" if idx != -1 else "UNGROUNDED",
            "start_offset": idx if idx != -1 else 0,
            "end_offset": (idx + len(quote)) if idx != -1 else len(quote),
        }
    }

    entity_upserts: List[Dict[str, Any]] = []
    relationship_signals: List[Dict[str, Any]] = []
    orgs_added: Set[str] = set()

    for idx_att, (name, org) in enumerate(attendees, start=1):
        clean_name = re.sub(r"\s*\([^)]*\)", "", name).strip()
        is_endpoint = any(t in name.lower() for t in ("mole-vc", "mole_vc", "nea-mole", "vc", "endpoint", "conference room"))
        if is_endpoint:
            ep_slug = re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")
            ep_id = f"system:{ep_slug}"
            entity_upserts.append({
                "id": ep_id,
                "type": "system",
                "name": clean_name,
                "description": f"Conference room video conferencing endpoint ({clean_name}).",
                "mutation": "CONFIRM",
                "evidence_ids": [evidence_id],
            })
            o_slug = re.sub(r"[^a-z0-9]+", "-", org.lower()).strip("-")
            o_id = f"org:{o_slug}"
            if o_id not in orgs_added:
                entity_upserts.append({
                    "id": o_id,
                    "type": "org",
                    "name": org,
                    "mutation": "CONFIRM",
                    "evidence_ids": [evidence_id],
                })
                orgs_added.add(o_id)
            relationship_signals.append({
                "signal_id": f"sig-{idx_att:02d}",
                "from_id": ep_id,
                "to_id": o_id,
                "predicate": "HOSTED_BY",
                "relationship": "HOSTED_BY",
                "evidence_ids": [evidence_id],
            })
            continue

        p_slug = re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")
        p_id = f"person:{p_slug}"
        entity_upserts.append({
            "id": p_id,
            "type": "person",
            "name": clean_name,
            "description": f"{clean_name} serving as delivery stakeholder for {org}.",
            "mutation": "CONFIRM",
            "evidence_ids": [evidence_id],
        })

        o_slug = re.sub(r"[^a-z0-9]+", "-", org.lower()).strip("-")
        o_id = f"org:{o_slug}"
        if o_id not in orgs_added:
            entity_upserts.append({
                "id": o_id,
                "type": "org",
                "name": org,
                "mutation": "CONFIRM",
                "evidence_ids": [evidence_id],
            })
            orgs_added.add(o_id)

        relationship_signals.append({
            "signal_id": f"sig-{idx_att:02d}",
            "from_id": p_id,
            "to_id": o_id,
            "predicate": "MEMBER_OF",
            "relationship": "MEMBER_OF",
            "evidence_ids": [evidence_id],
        })

    proj_name = metadata.get("project_name") or metadata.get("project") or "enterprise-system"
    proj_slug = re.sub(r"[^a-z0-9]+", "-", str(proj_name).lower()).strip("-")
    sys_id = f"system:{proj_slug}"
    entity_upserts.append({
        "id": sys_id,
        "type": "system",
        "name": str(proj_name),
        "mutation": "CONFIRM",
        "evidence_ids": [evidence_id],
    })
    first_p_id = entity_upserts[0]["id"]
    relationship_signals.append({
        "signal_id": f"sig-{len(relationship_signals) + 1:02d}",
        "from_id": first_p_id,
        "to_id": sys_id,
        "predicate": "CONTRIBUTES_TO",
        "relationship": "CONTRIBUTES_TO",
        "evidence_ids": [evidence_id],
    })

    dec_id = f"decision:{re.sub(r'[^a-z0-9]+', '-', meeting_id).strip('-')[:30]}"
    projection = {
        "schema_version": "5.7",
        "meeting_id": meeting_id,
        "classification": metadata.get("classification", "confidential"),
        "audiences": metadata.get("audiences", ["nee-engineering", "partner_advisory-delivery"]),
        "expected_base_graph_version": "1.0.0",
        "evidence_only_statement_ids": [],
        "evidence_index": evidence_index,
        "graph_mutations": {
            "entity_upserts": entity_upserts,
            "relationship_signals": relationship_signals,
            "decision_updates": [
                {
                    "decision_id": dec_id,
                    "status": "APPROVED",
                    "rationale": quote[:200],
                    "evidence_ids": [evidence_id],
                }
            ],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
    }
    return json.dumps(projection, indent=2)


def generate_offline_graph(
    system_prompt_or_prompt: str,
    user_content_or_metadata: Any = None,
    legacy_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a deterministic, schema-conformant Gate-3-compliant graph projection payload."""
    if isinstance(user_content_or_metadata, str):
        document = _document_fixture(user_content_or_metadata)
        if document is not None:
            return document
    metadata = _extract_metadata(user_content_or_metadata, legacy_metadata)
    project = str(metadata.get("project") or "project_lens")
    meeting_raw = str(metadata.get("meeting_name") or metadata.get("title") or "meeting")
    date_raw = str(metadata.get("date") or "09/25/2026").replace("/", "-")
    slug = re.sub(r"[^a-z0-9_]+", "", meeting_raw.lower().replace(" ", "_"))
    meeting_id = f"meeting-{date_raw}-{project}-{slug}"

    if _is_0925_sow(slug, date_raw, metadata):
        return _build_0925_sow_projection(meeting_id, metadata)
    return _build_generic_meeting_projection(meeting_id, metadata, user_content_or_metadata)
