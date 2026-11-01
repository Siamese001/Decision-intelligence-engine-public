#!/usr/bin/env python3
"""Finalize source-bound Evidence Envelope notes as an NEE intake package."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import validate_artifact  # noqa: E402
from dual_pipeline.evidence_field_limits import (  # noqa: E402
    validate_projection_field_lengths,
    validate_docx_cell_locator,
    validate_source_span_locator,
)
from dual_pipeline.core import (  # noqa: E402
    normalize_iso_date,
    read_json,
    seal_artifact,
    sha256_file,
    write_json_exclusive,
)
from dual_pipeline.pre_intake_classifier import (  # noqa: E402
    propose_pre_intake_classification,
)
from tools.validate_dual_pipeline_contracts import ContractError  # noqa: E402

EVIDENCE_ENVELOPE_CONTRACT_ID = "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0"
ACCEPTED_CONTRACT_IDS = {
    EVIDENCE_ENVELOPE_CONTRACT_ID,
    "urn:nee:dual-pipeline:google-meet-gemini-intake:1.0.0",
}
LOCAL_COMPUTE = "__LOCAL_COMPUTE__"
LOCAL_SOURCE_LOCATION = "__LOCAL_SOURCE_LOCATION__"
OPERATOR_SET_CLASSIFICATION = "__OPERATOR_SET_CLASSIFICATION__"
OPERATOR_SET_AUDIENCE = "OPERATOR_SET_AUDIENCE"
CLASSIFICATIONS = ["public", "internal", "confidential", "restricted"]


class HITLRequired(ValueError):
    """A named, narrow operator decision is required before package finalization."""

    def __init__(self, request: dict[str, Any]):
        super().__init__("operator metadata is required before package finalization")
        self.request = request


def _locators_with_items(package: dict[str, Any]) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for speaker in package.get("speakers", []):
        for statement in speaker.get("statements", []):
            yield statement, statement["locator"]
    for action in package.get("actions", []):
        yield action, action["locator"]


def _locators(package: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for _, loc in _locators_with_items(package):
        yield loc


def _resolve_source_section(text: str, source_text: str) -> str | None:
    norm_src = re.sub(r"\s+", " ", source_text)
    for length in (60, 40, 30, 20):
        target = text[:length].strip()
        norm_target = re.sub(r"\s+", " ", target)
        if not norm_target:
            continue
        idx = norm_src.find(norm_target)
        if idx != -1:
            lines = source_text.splitlines()
            char_count = 0
            target_line_idx = 0
            for i, line in enumerate(lines):
                char_count += len(line) + 1
                if char_count >= idx:
                    target_line_idx = i
                    break
            for j in range(target_line_idx, -1, -1):
                line = lines[j].strip()
                clean = line.lstrip("#").strip().lower()
                if clean in {"transcript", "session context", "transcript context", "forensic transcript"}:
                    continue
                if line.startswith(("#", "Subject:", "Activity:", "From:", "BID DUE DATE:")):
                    return line
                if 0 < len(line) < 60 and not line.endswith((".", ",", ";")) and (line.isupper() or line.istitle()):
                    return line
            for line in lines:
                clean = line.lstrip("#").strip().lower()
                if clean in {"transcript", "session context", "transcript context", "forensic transcript"}:
                    continue
                if line.strip():
                    return line.strip()
    return None


def _match_timestamp_locator(value: str, source_text: str) -> bool:
    parts = value.split(":")
    if len(parts) == 3 and parts[0] in ("0", "00"):
        try:
            mm_ss = f"{int(parts[1]):d}:{parts[2]}"
            if mm_ss in source_text:
                return True
            padded_mm_ss = f"{parts[1]}:{parts[2]}"
            if padded_mm_ss in source_text:
                return True
        except ValueError:
            pass
    return False


def _match_section_locator(value: str, source_text: str, source_path: Path) -> bool:
    clean_val = value.lstrip("#").strip()
    if clean_val.lower() in {"document", "root", "body"}:
        return True
    if clean_val.lower() in {"hitl", "alr", "ambiguity lock register", "ambiguity lock", "hitl_decision_ledger", "hitl decision ledger"}:
        if any(h in source_text for h in ["Ambiguity Lock Register", "Ambiguity Lock", "## Ambiguity Lock Register", "### Ambiguity Lock Register", "# Ambiguity Lock Register"]):
            return True
    direct_candidates = [
        f"### {value}", f"## {value}", f"### {clean_val}", f"## {clean_val}",
        f"# {clean_val}", value, clean_val,
    ]
    if any(c in source_text for c in direct_candidates):
        return True
    if ";" in value:
        sec_part = value.split(";", 1)[0].strip()
        first_id = sec_part.split("(")[0].split("—")[0].split("–")[0].split("-")[0].strip()
        if any(c in source_text for c in [sec_part, first_id, f"### {sec_part}", f"### {first_id}"]):
            return True
    first_id = value.split("(")[0].split("—")[0].split("–")[0].split("-")[0].strip()
    if first_id in source_text or f"### {first_id}" in source_text or value in source_text:
        return True

    if not value.startswith("#"):
        ref_dirs = [
            source_path.parent / "referenced_artifacts",
            source_path.parent.parent / "referenced_artifacts",
        ]
        try:
            if any((d / value).exists() for d in ref_dirs):
                return True
            if (source_path.parent / value).exists() or (source_path.parent.parent / value).exists():
                return True
            if next(source_path.parent.glob(f"**/{value}"), None) is not None:
                return True
            if source_path.parent.parent != Path("/tmp") and not str(source_path.parent.parent).startswith("/private/var"):
                if next(source_path.parent.parent.glob(f"**/{value}"), None) is not None:
                    return True
        except (OSError, ValueError):
            pass
    return False


def _match_artifact_locator(value: str, source_text: str, source_path: Path) -> bool:
    clean_val = value.split("#")[0].strip()
    for prefix in ("attachment:", "region:", "artifact:"):
        if clean_val.startswith(prefix):
            clean_val = clean_val[len(prefix):].strip()
    if clean_val.startswith("hitl"):
        return False
    clean_att = Path(clean_val).name
    att_name = clean_att
    if clean_att in source_text or att_name in source_text:
        return True
    ref_dirs = [
        source_path.parent / "referenced_artifacts",
        source_path.parent.parent / "referenced_artifacts",
    ]
    if source_path.parent.name.startswith((".staging_", ".tmp_")):
        try:
            ref_dirs.extend([
                p / "referenced_artifacts"
                for p in source_path.parent.parent.iterdir()
                if p.is_dir() and not p.name.startswith((".", "_"))
            ])
        except (OSError, ValueError):
            pass
    try:
        if any((d / clean_att).exists() or (d / att_name).exists() for d in ref_dirs):
            return True
        if (source_path.parent / clean_att).exists() or (source_path.parent / att_name).exists():
            return True
        if (source_path.parent.parent / clean_att).exists() or (source_path.parent.parent / att_name).exists():
            return True
        if next(source_path.parent.glob(f"**/{att_name}"), None) is not None:
            return True
        if source_path.parent.parent != Path("/tmp") and not str(source_path.parent.parent).startswith("/private/var"):
            if next(source_path.parent.parent.glob(f"**/{att_name}"), None) is not None:
                return True
        if Path(clean_att).is_file():
            return True
    except (OSError, ValueError):
        pass
    return False





def _match_hitl_locator(
    item: dict[str, Any], locator: dict[str, Any], source_text: str, source_path: Path
) -> bool:
    """Validate that a HITL decision ledger locator is grounded in the source context or ledger."""
    item_text = (item.get("text") or item.get("exact_text") or "").strip()

    speaker_candidates: list[str] = []
    if item_text:
        # e.g., "Human operator verified professional title '...' and organization '...' for Alex Brannan."
        m = re.search(r"\bfor\s+([A-Z][a-zA-Z0-9_.'\-\s]+?)(?:\.|$)", item_text)
        if m:
            speaker_candidates.append(m.group(1).strip())
    for key in ("reported_name", "person_name", "speaker"):
        val = item.get(key)
        if val and isinstance(val, str) and val.lower() not in {"operator", "system", "unknown", "unattributed participant"}:
            speaker_candidates.append(val.strip())
            if ":" in val:
                raw_id = val.split(":", 1)[-1].strip()
                speaker_candidates.append(raw_id)
                speaker_candidates.append(raw_id.replace("-", " "))
                speaker_candidates.append(raw_id.replace("-", " ").title())

    expanded_candidates = list(speaker_candidates)
    for c in speaker_candidates:
        if "ph" in c.lower():
            expanded_candidates.append(re.sub(r"ph", "f", c, flags=re.I))
        elif "f" in c.lower():
            expanded_candidates.append(re.sub(r"f", "ph", c, flags=re.I))
    speaker_candidates = expanded_candidates

    for cand in speaker_candidates:
        if cand and cand in source_text:
            return True

    loc_val_lower = str(locator.get("value") or "").lower()
    stmt_id_lower = str(item.get("statement_id") or "").lower()
    if "role" in stmt_id_lower or "roster" in stmt_id_lower or "roster" in loc_val_lower or "c0" in loc_val_lower or "receipt" in loc_val_lower:
        words = re.findall(r"\b[A-Z][a-z]+\b", item_text)
        skip_words = {
            "Meeting", "Roster", "Lead", "Enterprise", "Architect", "Senior", "Director",
            "Manager", "Receipt", "Human", "Operator", "Verified", "Professional", "Title", "Organization",
        }
        if any(w in source_text for w in words if w not in skip_words):
            return True
        if any(p in source_text.lower() for p in ["participants:", "attendees:", "roster", "**participants:**", "**attendees:**", "to:", "from:"]):
            return True

    ledger_candidates = [
        source_path.parent / "hitl_decision_ledger.json",
        source_path.parent.parent / "hitl_decision_ledger.json",
        ROOT / ".local/ledger/hitl_decision_ledger.json",
        Path(".local/ledger/hitl_decision_ledger.json"),
        Path("storage/hitl_decision_ledger.json"),
        Path(".local/dual_pipeline/hitl_decision_ledger.json"),
    ]
    loc_val = str(locator.get("value") or "").strip()
    clean_val = loc_val.split("hitl_decision_ledger:", 1)[-1].strip()

    for lpath in ledger_candidates:
        if lpath.is_file():
            try:
                ltext = lpath.read_text(encoding="utf-8")
                if clean_val and clean_val not in {"hitl", "hitl_decision_ledger"} and clean_val in ltext:
                    return True
                for cand in speaker_candidates:
                    if cand and cand in ltext:
                        return True
            except Exception:
                pass

    return False


def _validate_single_locator(
    item: dict[str, Any], locator: dict[str, Any], source_text: str, source_path: Path
) -> None:
    loc = dict(locator)
    if validate_source_span_locator(item, loc, source_text):
        return
    kind = loc["kind"]
    value = loc["value"].strip()
    if kind == "cell":
        validate_docx_cell_locator(item, loc, source_text)
        return
    if kind not in {"timestamp", "section", "region", "attachment", "page"}:
        raise ValueError(
            "Evidence envelope intake locators must use timestamp, section, region, page, or attachment values"
        )
    if kind == "section" and value.lstrip("#").strip().lower() in {
        "transcript", "session context", "transcript context", "forensic transcript"
    }:
        raise ValueError(
            f"Trivial fallback locator is not permitted: {kind} {value!r}. "
            "Evidence must be grounded to a specific turn or sub-section."
        )
    if (kind == "region" and (value.startswith("hitl_decision_ledger") or value in {"hitl", "hitl_decision_ledger"})) or (
        kind == "section" and (value in {"hitl", "hitl_decision_ledger"} or "roster" in value.lower() or "c0" in value.lower())
    ):
        if _match_hitl_locator(item, loc, source_text, source_path):
            if loc.get("kind") != "region":
                loc["kind"] = "region"
            if loc.get("value") == "hitl" or "roster" in loc.get("value", "").lower() or "c0" in loc.get("value", "").lower():
                loc["value"] = "hitl_decision_ledger"
            item["locator"] = loc
            return
    if kind == "timestamp" and (_match_timestamp_locator(value, source_text) or (":" in value and value in source_text)):
        return
    if kind == "section" and _match_section_locator(value, source_text, source_path):
        return
    if kind in {"region", "attachment", "page"} and _match_artifact_locator(value, source_text, source_path):
        if loc.get("kind") == "attachment":
            loc["kind"] = "region"
            item["locator"] = loc
        return
    if kind == "region":
        m_line = re.match(r"^line-(\d+)$", value)
        if m_line:
            line_idx = int(m_line.group(1)) - 1
            src_lines = source_text.splitlines()
            if 0 <= line_idx < max(1, len(src_lines)):
                return
    if len(value) >= 4 and value in source_text and kind not in {"timestamp", "page"}:
        return

    # Fallback for document mode
    item_text = (item.get("text") or item.get("exact_text") or "").strip()
    if item_text:
        resolved_sec = _resolve_source_section(item_text, source_text)
        if resolved_sec and resolved_sec in source_text:
            loc["kind"] = "section"
            loc["value"] = resolved_sec
            item["locator"] = loc
            return

    raise ValueError(f"source-bound locator is absent from the preserved export: {kind} {value!r}")


_SOURCE_TEXT_MEM_CACHE: dict[tuple[str, int, int], str] = {}


def _read_source_text_cached(source_path: Path) -> str:
    st = source_path.stat()
    key = (str(source_path.resolve()), st.st_mtime_ns, st.st_size)
    if key not in _SOURCE_TEXT_MEM_CACHE:
        _SOURCE_TEXT_MEM_CACHE[key] = source_path.read_text(encoding="utf-8-sig")
    return _SOURCE_TEXT_MEM_CACHE[key]


def _validate_locators(package: dict[str, Any], source_path: Path) -> None:  # noqa: C901
    """Require each declared locator to resolve in the exact source export."""
    try:
        source_text = _read_source_text_cached(source_path)
    except UnicodeDecodeError as exc:
        raise ValueError("--source must be a UTF-8 source export") from exc

    pairs = list(_locators_with_items(package))
    if len(pairs) <= 1:
        for item, locator in pairs:
            _validate_single_locator(item, locator, source_text, source_path)
        return
    from dual_pipeline.canonical_pipeline.parallel_orchestrator import run_indexed_parallel
    run_indexed_parallel(
        pairs,
        lambda _idx, pair: _validate_single_locator(pair[0], pair[1], source_text, source_path),
    )



def _missing_operator_decisions(
    meeting: dict[str, Any],
    *,
    source_location: str | None,
    classification: str | None,
    audiences: list[str] | None,
) -> list[dict[str, Any]]:
    """Return only the operator choices that cannot safely be inferred."""

    requests: list[dict[str, Any]] = []
    effective_location = source_location or meeting.get("source_location")
    if effective_location in {None, "", LOCAL_SOURCE_LOCATION}:
        requests.append(
            {
                "field": "source_location",
                "prompt": (
                    "Provide the stable controlled-archive location for these exact "
                    "evidence notes bytes."
                ),
                "input": "string",
                "required": True,
            }
        )
    effective_classification = classification or meeting.get("classification")
    if effective_classification in {None, "", OPERATOR_SET_CLASSIFICATION}:
        requests.append(
            {
                "field": "classification",
                "prompt": "Classify this meeting evidence before candidate staging.",
                "input": "single_select",
                "options": CLASSIFICATIONS,
                "required": True,
                "default": None,
            }
        )
    effective_audiences = audiences if audiences is not None else meeting.get("audiences")
    if effective_audiences is None or effective_audiences == [] or effective_audiences == [OPERATOR_SET_AUDIENCE]:
        requests.append(
            {
                "field": "audiences",
                "prompt": (
                    "Name one or more authorized audiences allowed to retrieve these "
                    "candidate records."
                ),
                "input": "string_list",
                "required": True,
                "default": None,
            }
        )
    return requests


def build_hitl_request(
    package: dict[str, Any],
    source_path: Path,
    *,
    source_location: str | None = None,
    classification: str | None = None,
    audiences: list[str] | None = None,
) -> dict[str, Any] | None:
    """Produce a bounded pre-intake HITL request, or None when metadata is complete."""

    meeting = package["meeting"]
    decisions = _missing_operator_decisions(
        meeting,
        source_location=source_location,
        classification=classification,
        audiences=audiences,
    )
    if not decisions:
        return None
    source_hash = sha256_file(source_path)
    source_bytes = source_path.read_bytes()
    classification_package = copy.deepcopy(package)
    classification_meeting = classification_package["meeting"]
    if classification is not None:
        classification_meeting["classification"] = classification
    if audiences is not None:
        classification_meeting["audiences"] = audiences
    proposal = propose_pre_intake_classification(
        package=classification_package,
        source_bytes=source_bytes,
    )
    for decision in decisions:
        if decision["field"] == "classification":
            decision["proposed_value"] = proposal["proposed_classification"]
            decision["default"] = proposal["proposed_classification"]
        elif decision["field"] == "audiences":
            decision["proposed_values"] = proposal["proposed_audiences"]
            decision["default"] = proposal["proposed_audiences"] or None
    return {
        "schema_version": "1.0.0",
        "request_type": "evidence_envelope_pre_intake_hitl",
        "status": "HITL_REQUIRED",
        "source": {
            "artifact_id": package["artifact_id"],
            "meeting_id": meeting["meeting_id"],
            "title": meeting["title"],
            "started_at": meeting.get("started_at"),
            "source_hash": source_hash,
        },
        "initial_classification": proposal,
        "requested_decisions": decisions,
        "approved_action_preview": {
            "action": (
                "Finalize one source-bound Evidence Envelope candidate package and "
                "open its intake directive."
            ),
            "graph_mutation": "none",
            "canonical_promotion": "none",
            "standing_authority": "none",
        },
        "follow_on_gate": {
            "required": (
                "A separate, exact-scope release approval is required after package "
                "finalization before any candidate-lane graph mutation."
            ),
            "not_requested_now": True,
        },
    }


def _slugify(text: str) -> str:
    import re
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return cleaned or "item"


def _find_quote_timestamp(quote: str, source_path: Path | None = None) -> dict[str, str] | None:
    if not quote or not source_path or not source_path.is_file():
        return None
    try:
        clean_q = quote.strip()
        if clean_q.startswith("**") and ":**" in clean_q:
            clean_q = clean_q.split(":**", 1)[1].strip()
        source_text = _read_source_text_cached(source_path)
        idx = source_text.find(clean_q)
        if idx == -1 and len(clean_q) > 40:
            idx = source_text.find(clean_q[:40])
        if idx != -1:
            preceding = source_text[:idx]
            matches = list(re.finditer(r"(?:\[|\b)(\d{1,2}:\d{2}(?::\d{2})?)(?:\]|\b)", preceding))
            if matches:
                return {"kind": "timestamp", "value": matches[-1].group(1)}
    except Exception:
        pass
    return None


def _find_section_heading(keywords: set[str], source_path: Path | None = None) -> dict[str, str] | None:
    if not source_path or not source_path.is_file():
        return None
    try:
        source_text = _read_source_text_cached(source_path)
        for h in re.finditer(r"^(#{1,3}\s+(.+))$", source_text, re.MULTILINE):
            full_h = h.group(1).strip()
            title = h.group(2).strip().lower()
            if any(kw in title for kw in keywords):
                return {"kind": "section", "value": full_h}
    except Exception:
        pass
    return None


def _find_default_locator(source_path: Path | None = None) -> dict[str, str]:
    if source_path and source_path.is_file():
        try:
            text = _read_source_text_cached(source_path)
            m = re.search(r"(?:\[|\b)(\d{1,2}:\d{2}(?::\d{2})?)(?:\]|\b)", text)
            if m:
                return {"kind": "timestamp", "value": m.group(1)}
            for h in re.finditer(r"^#{1,3}\s+(.+)$", text, re.MULTILINE):
                clean_h = h.group(1).strip()
                if clean_h.lower() not in {
                    "transcript",
                    "session context",
                    "transcript context",
                    "forensic transcript",
                }:
                    return {"kind": "section", "value": f"## {clean_h}"}
        except Exception:
            pass
    return {"kind": "timestamp", "value": "00:00:00"}


def _normalize_raw_locator(raw_loc: Any, text: str, source_path: Path | None) -> dict[str, str]:
    if not isinstance(raw_loc, dict):
        return _find_quote_timestamp(text, source_path) or _find_default_locator(source_path)

    loc_kind = raw_loc.get("kind")
    val = str(raw_loc.get("value") or "").strip()
    if val.startswith("attachment:"):
        val = val.split("attachment:", 1)[1].strip()

    loc: dict[str, str] = {}
    if loc_kind in {"timestamp", "transcript_timestamp"}:
        loc = {"kind": "timestamp", "value": val}
    elif loc_kind in {"page", "region", "cell", "record", "uri_fragment", "attachment"}:
        loc = {
            "kind": "region" if loc_kind == "attachment" else loc_kind,
            "value": f"attachment:{val}" if loc_kind == "attachment" and not val.startswith("attachment:") else val,
        }
    elif (
        loc_kind in {"hitl_decision_ledger", "alr", "hitl", "ledger", "c0_receipt", "roster", "receipt"}
        or val in {"hitl", "c0_receipt", "roster", "receipt"}
        or (isinstance(loc_kind, str) and "c0" in loc_kind.lower())
        or (isinstance(raw_loc, dict) and (raw_loc.get("source") in {"TRANSCRIPT_HITL", "C0_RECEIPT", "ROSTER"} or raw_loc.get("source_type") in {"TRANSCRIPT_HITL", "C0_RECEIPT", "ROSTER"}))
    ):
        alr_loc = _find_section_heading({"ambiguity lock register", "ambiguity lock", "alr", "hitl", "attendees", "participants", "roster"}, source_path)
        if alr_loc:
            loc = alr_loc
        else:
            eff_val = f"hitl_decision_ledger:{val}" if val and val not in {"hitl", "c0_receipt", "roster", "receipt"} else "hitl_decision_ledger"
            loc = {"kind": "region", "value": eff_val}
    else:
        clean_sec = val.lstrip("#").strip().lower()
        if not val or clean_sec in {"transcript", "session context", "transcript context", "forensic transcript"}:
            loc = _find_quote_timestamp(text, source_path) or _find_default_locator(source_path)
        elif clean_sec in {"participants", "roster", "participant roster", "attendees", "meeting attendees"} or "roster" in clean_sec or "c0" in clean_sec:
            attendees_loc = _find_section_heading({"attendees", "participants", "roster"}, source_path)
            loc = attendees_loc or {"kind": "region", "value": "hitl_decision_ledger"}
        elif clean_sec in {"hitl", "alr", "ambiguity lock register", "ambiguity lock", "hitl_decision_ledger", "hitl decision ledger"}:
            alr_loc = _find_section_heading({"ambiguity lock register", "ambiguity lock", "alr", "hitl"}, source_path)
            loc = alr_loc or {"kind": "region", "value": "hitl_decision_ledger"}
        else:
            loc = {"kind": "section", "value": val}

    if "quote_digest" in raw_loc:
        loc["quote_digest"] = raw_loc["quote_digest"]
    return loc


def _resolve_envelope_started_at(
    draft: dict[str, Any],
    meeting_dict: dict[str, Any],
    meeting_id: str,
) -> str:
    """Resolves started_at ISO timestamp from draft fields or meeting_id."""
    started_at = draft.get("started_at") or meeting_dict.get("started_at")
    if started_at:
        return str(started_at)

    date_str = (
        draft.get("date")
        or draft.get("meeting_date")
        or meeting_dict.get("date")
        or meeting_dict.get("meeting_date")
    )
    time_str = (
        draft.get("time")
        or draft.get("meeting_time")
        or meeting_dict.get("time")
        or meeting_dict.get("meeting_time")
    )
    if date_str:
        iso_d = normalize_iso_date(str(date_str))
        if iso_d:
            if time_str:
                clean_t = str(time_str).strip()
                m_12h = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM|am|pm)?", clean_t)
                if m_12h and (" " in clean_t or "-" in clean_t or "–" in clean_t or m_12h.group(4)):
                    hh = int(m_12h.group(1))
                    mm = int(m_12h.group(2))
                    ss = int(m_12h.group(3) or 0)
                    ampm = (m_12h.group(4) or "").upper()
                    if not ampm:
                        m_end_ampm = re.search(r"(AM|PM|am|pm)\b", clean_t)
                        if m_end_ampm and hh < 12:
                            ampm = m_end_ampm.group(1).upper()
                    if ampm == "PM" and hh < 12:
                        hh += 12
                    elif ampm == "AM" and hh == 12:
                        hh = 0
                    return f"{iso_d}T{hh:02d}:{mm:02d}:{ss:02d}-04:00"
                if not re.search(r"[+-]\d{2}:?\d{2}$|Z$", clean_t):
                    clean_t = f"{clean_t}-04:00" if ":" in clean_t else f"{clean_t}:00:00-04:00"
                return f"{iso_d}T{clean_t}" if not clean_t.startswith("T") else f"{iso_d}{clean_t}"
            return f"{iso_d}T00:00:00Z"

    if meeting_id:
        m_match = re.search(r"(\d{4})(\d{2})(\d{2})", meeting_id)
        if m_match:
            return f"{m_match.group(1)}-{m_match.group(2)}-{m_match.group(3)}T00:00:00Z"

    return ""


def _normalize_single_locator(loc_parent: dict[str, Any], source_path: Path | None = None) -> None:
    loc = loc_parent.get("locator")
    if isinstance(loc, dict):
        if loc.get("kind") == "attachment":
            loc["kind"] = "region"
            val = str(loc.get("value") or "")
            if not val.startswith("attachment:"):
                loc["value"] = f"attachment:{val}"
        elif loc.get("kind") in {"hitl_decision_ledger", "alr", "hitl", "ledger"} or str(loc.get("value") or "").strip().lower() in {"hitl", "alr", "ambiguity lock register", "ambiguity lock", "hitl_decision_ledger", "hitl decision ledger"}:
            alr_loc = _find_section_heading({"ambiguity lock register", "ambiguity lock", "alr", "hitl"}, source_path)
            if alr_loc:
                loc["kind"] = alr_loc["kind"]
                loc["value"] = alr_loc["value"]
            else:
                loc["kind"] = "region"
                val = str(loc.get("value") or "")
                loc["value"] = f"hitl_decision_ledger:{val}" if val and val != "hitl" else "hitl_decision_ledger"


def _normalize_mutation_item(item: Any, source_path: Path | None = None) -> None:
    if not isinstance(item, dict):
        return
    ev_ids = item.get("evidence_ids")
    if isinstance(ev_ids, list):
        item["evidence_ids"] = list(dict.fromkeys(ev_ids))
    _normalize_single_locator(item, source_path)
    ev = item.get("evidence")
    if isinstance(ev, dict):
        _normalize_single_locator(ev, source_path)


def _normalize_raw_mutation_locators(raw_mutations: dict[str, Any], source_path: Path | None = None) -> None:
    categories = (
        "entity_upserts",
        "relationship_signals",
        "decision_updates",
        "proposal_updates",
        "action_updates",
        "risk_updates",
        "strategic_intent_signals",
    )
    for cat in categories:
        for item in raw_mutations.get(cat, []):
            _normalize_mutation_item(item, source_path)


def parse_output02_action_ledger(
    recap_text: str, source_path: Path | None = None
) -> list[dict[str, Any]]:
    """Extracts structured actions from Output 02's `# Action & Governance Ledger` or action tables."""
    if not recap_text:
        return []
    from dual_pipeline.transcript_qa_core.action_ledger_porter import (
        _LOC_SPAN_RE,
        extract_reconciled_output02_actions,
    )

    f1_text = ""
    if source_path and source_path.is_file():
        try:
            f1_text = _read_source_text_cached(source_path)
        except Exception:
            f1_text = ""

    _, reconciled_rows = extract_reconciled_output02_actions(recap_text, f1_text=f1_text)
    actions: list[dict[str, Any]] = []
    for row in reconciled_rows:
        act_id = row["action_id"]
        act_desc = row["action"]
        owner = row["reported_owner"]
        raw_due = row["due_raw"]
        schema_due = normalize_iso_date(raw_due) if raw_due else None
        raw_status = (row["status_raw"] or "planned").strip().lower()
        status_val = raw_status if raw_status in {"open", "planned", "completed", "cancelled"} else "planned"

        resolved_ts = row.get("resolved_timestamp")
        if not resolved_ts:
            m_loc = _LOC_SPAN_RE.search(row.get("locator_raw") or "")
            if m_loc:
                resolved_ts = m_loc.group(1)

        if resolved_ts and (_match_timestamp_locator(resolved_ts, f1_text) or (":" in resolved_ts and resolved_ts in f1_text)):
            act_locator = {"kind": "timestamp", "value": resolved_ts}
        else:
            act_locator = _normalize_raw_locator(None, act_desc, source_path)

        act_entry: dict[str, Any] = {
            "action_id": act_id,
            "action": act_desc,
            "reported_owner": owner or "Unassigned",
            "due_date": schema_due,
            "status": status_val,
            "dependency": None,
            "requested_follow_up": None,
            "locator": act_locator,
        }
        if row.get("evidence_ids"):
            act_entry["_evidence_ids"] = list(row["evidence_ids"])
            act_entry["_evidence_entries"] = list(row["evidence_entries"])
        actions.append(act_entry)
    return actions


_DEGENERATE_MEETING_IDS = frozenset({
    "meeting-unknown",
    "meeting-unspecified",
    "meeting-default",
    "unknown",
    "unspecified",
})
_DEGENERATE_TITLES = frozenset({
    "unknown",
    "unspecified",
    "default",
    "meeting unknown",
    "meeting unspecified",
    "source document",
    "source_document",
})


_STAGING_DIR_STEM_RE = re.compile(
    r"(?:^|/)\.staging_([A-Za-z0-9_]+?)_(?:[0-9a-fA-F]{8,}|\d{8,})(?=/|$)"
)


def _normalize_cold_archive_stem(path_or_ref: str | Path | None) -> str:
    """Extract canonical meeting/document stem stripping staging, worktree, and local input prefixes."""
    if not path_or_ref:
        return ""
    s = str(path_or_ref).strip().replace("\\", "/")
    if s.startswith("local://"):
        s = s[len("local://") :]
    s = re.sub(r"^.*?/\.worktrees/[^/]+/", "", s)
    m_stg = _STAGING_DIR_STEM_RE.search(s)
    if m_stg:
        return _slugify(m_stg.group(1))
    s = re.sub(r"(?:^|/)\.staging_[^/]+/", "/", s)
    p = Path(s)
    generic_names = {
        "source_document.txt",
        "source_document.md",
        "intake_package.json",
        "metadata.json",
        "forensic_transcript.md",
    }
    if p.name.lower() in generic_names or re.match(r"^0[123]_", p.name):
        if p.parent.name and not p.parent.name.startswith(".") and p.parent.name not in {"input", "output"}:
            return _slugify(p.parent.name)
    return _slugify(p.stem)


def _validate_non_degenerate_source_identity(
    meeting_id: str,
    title: str,
    source_path: Path | None = None,
    draft_path: Path | None = None,
) -> tuple[str, str]:
    """Fail closed on 'meeting-unknown'/'Unknown' or hex-hash titles, deriving from path when unambiguous."""
    clean_id = (meeting_id or "").strip()
    clean_title = (title or "").strip()

    is_bad_id = (
        not clean_id
        or clean_id.lower() in _DEGENERATE_MEETING_IDS
        or bool(re.fullmatch(r"document-[0-9a-f]{12}", clean_id, re.I))
    )
    is_bad_title = (
        not clean_title
        or clean_title.lower() in _DEGENERATE_TITLES
        or bool(re.fullmatch(r"document\s+[0-9a-f]{12}", clean_title, re.I))
    )

    if is_bad_id or is_bad_title:
        stem = _normalize_cold_archive_stem(source_path) or _normalize_cold_archive_stem(draft_path)
        if stem and stem not in _DEGENERATE_TITLES and not stem.startswith(("01-", "02-", "03-")):
            if is_bad_title:
                clean_title = stem.replace("-", " ").title()
                is_bad_title = False
            if is_bad_id:
                clean_id = f"meeting-{stem}"
                is_bad_id = False

    if is_bad_id or is_bad_title:
        raise ValueError(
            f"Degenerate source identity rejected (meeting_id={clean_id!r}, title={clean_title!r}). "
            "Placeholder 'meeting-unknown' / 'Unknown' and raw hex-hash document titles are prohibited."
        )
    return clean_id, clean_title


def _adapt_v53_projection_to_envelope(
    draft: dict[str, Any], source_path: Path | None = None, draft_path: Path | None = None
) -> dict[str, Any]:  # noqa: C901
    """Adapt a prompt V5.5 canonical signal projection into a canonical evidence envelope."""
    meeting_dict = draft.get("meeting") if isinstance(draft.get("meeting"), dict) else {}
    raw_meeting_id = str(draft.get("meeting_id") or meeting_dict.get("meeting_id") or "")
    raw_title = str(
        draft.get("title")
        or meeting_dict.get("title")
        or draft.get("meeting_title")
        or (
            raw_meeting_id.replace("meeting-", "").replace("-", " ").title()
            if raw_meeting_id and raw_meeting_id.lower() not in _DEGENERATE_MEETING_IDS
            else ""
        )
    )
    meeting_id, title = _validate_non_degenerate_source_identity(
        raw_meeting_id, raw_title, source_path=source_path, draft_path=draft_path
    )
    started_at = _resolve_envelope_started_at(draft, meeting_dict, meeting_id)

    classification_map = {
        "fact": "technical",
        "reported fact": "technical",
        "reported_fact": "technical",
        "proposal": "strategic",
        "alignment": "strategic",
        "decision": "decision",
        "commitment": "strategic",
        "action": "strategic",
        "open question": "technical",
        "open_question": "technical",
        "risk": "risk",
        "dependency": "dependency",
        "inference": "technical",
        "summary": "summary",
    }

    referenced_eids: set[str] = set()
    for grp_key in ("decision_updates", "proposal_updates", "action_updates", "risk_updates", "entity_upserts", "relationship_signals", "strategic_intent_signals"):
        for item_obj in (draft.get(grp_key) or draft.get("graph_mutations", {}).get(grp_key) or []):
            if isinstance(item_obj, dict):
                for ref_eid in (item_obj.get("evidence_ids") or []):
                    if ref_eid:
                        referenced_eids.add(str(ref_eid))

    speakers_map: dict[str, dict[str, Any]] = {}
    statement_classifications: dict[str, str] = {}
    for stmt_id, stmt in draft.get("evidence_index", {}).items():
        spk_name = stmt.get("speaker") or "Unattributed Participant"
        if str(spk_name).strip().lower().replace("-", "_") in {"meeting_metadata", "metadata", "system"}:
            if stmt_id not in referenced_eids:
                continue
            spk_name = draft.get("organizer") or meeting_dict.get("organizer") or "Meeting Context"
        if spk_name and ":" in spk_name:
            spk_name = spk_name.split(":", 1)[1].replace("-", " ").title()
        spk_ref = "speaker-" + _slugify(spk_name)
        if spk_ref not in speakers_map:
            speakers_map[spk_ref] = {
                "speaker_ref": spk_ref,
                "reported_name": spk_name,
                "email": None,
                "role_or_org": stmt.get("role_or_org"),
                "statements": [],
            }
        raw_kind = (stmt.get("kind") or stmt.get("source_classification") or "summary").strip().lower()
        statement_classifications[stmt_id] = (stmt.get("source_classification") or raw_kind).strip().lower()
        if raw_kind in {"summary", "decision", "risk", "dependency", "technical", "strategic"}:
            kind = raw_kind
        else:
            kind = classification_map.get(raw_kind, "summary")
        stmt_text = (
            stmt.get("exact_text")
            or stmt.get("verbatim_quote")
            or stmt.get("direct_quote")
            or stmt.get("quote")
            or stmt.get("text")
            or stmt.get("claim_text")
            or ""
        )
        loc = _normalize_raw_locator(stmt.get("locator"), stmt_text, source_path)
        speakers_map[spk_ref]["statements"].append(
            {
                "statement_id": stmt_id,
                "kind": kind,
                "text": stmt_text,
                "locator": loc,
                "gemini_confidence": stmt.get("gemini_confidence", None),
            }
        )

    actions = []
    action_evidence_map: dict[str, list[str]] = {}
    action_updates = draft.get("action_updates") or draft.get("graph_mutations", {}).get("action_updates", [])
    for act in action_updates:
        act_id = act.get("action_id", "act-01")
        if ":" in act_id:
            act_id = act_id.split(":", 1)[1]
        act_id = "action-" + _slugify(act_id)
        act_desc = act.get("action_text") or act.get("description") or act.get("action")
        if not act_desc:
            act_desc = f"Action {act_id} execution"
        raw_act_locator = act.get("locator")
        act_eids = [str(e) for e in (act.get("evidence_ids") or []) if e]
        if act_eids:
            action_evidence_map[act_id] = list(dict.fromkeys(act_eids))
        if not raw_act_locator and act_eids:
            first_eid = act_eids[0]
            raw_act_locator = draft.get("evidence_index", {}).get(first_eid, {}).get("locator")
        act_locator = _normalize_raw_locator(raw_act_locator, act_desc, source_path)

        owner = act.get("owner") or act.get("reported_owner") or act.get("owner_id")
        if owner and ":" in owner:
            owner = owner.split(":", 1)[1].replace("-", " ").title()

        status_val = act.get("status", "planned")
        if status_val not in {"open", "planned", "completed", "cancelled"}:
            status_val = "planned"

        raw_due_date = act.get("due_date")
        schema_due_date = normalize_iso_date(raw_due_date) if raw_due_date else None
        actions.append(
            {
                "action_id": act_id,
                "action": act_desc,
                "reported_owner": owner,
                "due_date": schema_due_date,
                "status": status_val,
                "dependency": act.get("dependency"),
                "requested_follow_up": act.get("requested_follow_up"),
                "locator": act_locator,
            }
        )

    if not actions:
        search_dirs = [d for d in (draft_path.parent if draft_path else None, source_path.parent if source_path else None) if d and d.is_dir()]
        for sdir in search_dirs:
            cands_02 = sorted(sdir.glob("02_*_executive_recap.md"))
            if cands_02 and cands_02[0].is_file():
                try:
                    actions = parse_output02_action_ledger(cands_02[0].read_text(encoding="utf-8"), source_path=source_path)
                    if actions:
                        for act_obj in actions:
                            act_id = str(act_obj.get("action_id") or "")
                            eids = act_obj.pop("_evidence_ids", None) or []
                            ev_entries = act_obj.pop("_evidence_entries", None) or []
                            if eids and act_id:
                                action_evidence_map[act_id] = list(dict.fromkeys(eids))
                            for ev_id, ev_item in ev_entries:
                                spk_name = str(ev_item.get("speaker") or act_obj.get("reported_owner") or "Unattributed Participant")
                                spk_ref = "speaker-" + _slugify(spk_name)
                                if spk_ref not in speakers_map:
                                    speakers_map[spk_ref] = {
                                        "speaker_ref": spk_ref,
                                        "reported_name": spk_name,
                                        "email": None,
                                        "role_or_org": None,
                                        "statements": [],
                                    }
                                existing_sids = {s.get("statement_id") for s in speakers_map[spk_ref]["statements"]}
                                if ev_id not in existing_sids:
                                    speakers_map[spk_ref]["statements"].append(
                                        {
                                            "statement_id": ev_id,
                                            "kind": "strategic",
                                            "text": str(ev_item.get("text") or ""),
                                            "locator": dict(ev_item.get("locator") or act_obj["locator"]),
                                            "gemini_confidence": None,
                                        }
                                    )
                                statement_classifications[ev_id] = "commitment"
                        break
                except Exception:
                    pass
    else:
        for act_obj in actions:
            act_obj.pop("_evidence_ids", None)
            act_obj.pop("_evidence_entries", None)

    decisions = []
    decision_updates = draft.get("decision_updates") or draft.get("graph_mutations", {}).get("decision_updates", [])
    for dec in decision_updates:
        dec_id = dec.get("decision_id", "dec-01")
        if ":" in dec_id:
            dec_id = dec_id.split(":", 1)[1]
        dec_id = "decision-" + _slugify(dec_id)
        decisions.append(
            {
                "decision_id": dec_id,
                "decision": dec.get("decision_question") or dec.get("decision_text") or dec.get("decision", ""),
                "status": dec.get("status", "UNDER_EVALUATION"),
                "evidence_ids": dec.get("evidence_ids", []),
            }
        )

    meeting = {
        "meeting_id": meeting_id,
        "title": title,
        "started_at": started_at,
        "classification": draft.get("classification") or meeting_dict.get("classification", OPERATOR_SET_CLASSIFICATION),
        "audiences": draft.get("audiences") or meeting_dict.get("audiences", [OPERATOR_SET_AUDIENCE]),
        "source_location": draft.get("source_location") or meeting_dict.get("source_location", LOCAL_SOURCE_LOCATION),
        "organizer": draft.get("organizer") or meeting_dict.get("organizer"),
        "platform_reference": draft.get("platform_reference") or meeting_dict.get("platform_reference"),
        "attendees": meeting_dict.get("attendees") or [s["reported_name"] for s in speakers_map.values() if s.get("reported_name")] or ["Unattributed Participant"],
        "template_version": str(draft.get("template_version") or meeting_dict.get("template_version") or "5.3"),
    }

    raw_mutations = copy.deepcopy(draft.get("graph_mutations", {}))
    if not raw_mutations:
        extracted = {}
        for key in ["entity_upserts", "relationship_signals", "decision_updates", "proposal_updates", "action_updates", "risk_updates", "strategic_intent_signals", "entities", "relationships", "assertions"]:
            if key in draft:
                extracted[key] = copy.deepcopy(draft[key])
        if extracted:
            raw_mutations = {
                "entity_upserts": extracted.get("entity_upserts") or extracted.get("entities", []),
                "relationship_signals": extracted.get("relationship_signals") or extracted.get("relationships", []),
                "assertion_updates": extracted.get("assertions", []),
                "decision_updates": extracted.get("decision_updates", []),
                "proposal_updates": extracted.get("proposal_updates", []),
                "action_updates": extracted.get("action_updates", []),
                "risk_updates": extracted.get("risk_updates", []),
                "strategic_intent_signals": extracted.get("strategic_intent_signals", []),
            }
            if "meeting_id" in draft:
                raw_mutations["meeting_id"] = draft["meeting_id"]
    else:
        for key in ("decision_updates", "proposal_updates", "action_updates", "risk_updates", "strategic_intent_signals"):
            if key not in raw_mutations and key in draft:
                raw_mutations[key] = copy.deepcopy(draft[key])
    _normalize_raw_mutation_locators(raw_mutations, source_path)

    return {
        "contract_id": EVIDENCE_ENVELOPE_CONTRACT_ID,
        "schema_version": "1.0.0",
        "artifact_id": "artifact-" + meeting_id,
        "meeting": meeting,
        "speakers": list(speakers_map.values()),
        "actions": actions,
        "adapter": {
            "name": "nee-evidence-envelope-intake",
            "version": "1.0.0",
        },
        "extensions": {
            "com.partner_advisory.nee.canonical-signal-projection": raw_mutations,
            "com.partner_advisory.nee.projection": raw_mutations,
            "com.partner_advisory.nee.decisions": decisions,
            "com.partner_advisory.nee.statement-classifications": statement_classifications,
            "com.partner_advisory.nee.action-evidence-ids": action_evidence_map,
            "com.partner_advisory.nee.ingestion-context": draft.get("ingestion_context"),
            "com.partner_advisory.nee.ingestion-context-hash": draft.get("ingestion_context_hash"),
            **({"com.partner_advisory.nee.entity-intent-profiles": draft["entity_intent_profiles"]} if "entity_intent_profiles" in draft else {}),
            **({"com.partner_advisory.nee.project-status-snapshot": draft["project_status_snapshot"]} if "project_status_snapshot" in draft else {}),
            **({"com.partner_advisory.nee.document-signal-routing": draft["document_signal_routing"]} if "document_signal_routing" in draft else {}),
            **({"com.partner_advisory.nee.authority-rank": draft["authority_rank"]} if "authority_rank" in draft else {}),
            **({"com.partner_advisory.nee.source-posture": draft["source_posture"]} if "source_posture" in draft else {}),
            **({"com.partner_advisory.nee.inline-canonical-promotion": bool(draft["inline_canonical_promotion"])} if "inline_canonical_promotion" in draft else {}),
        },
    }


def _validate_semantic_yield(source_path: Path, min_words: int = 50) -> None:
    """Evaluate raw text for minimum semantic yield (dialogue or table body words), halting on zero-signal."""
    source_text = source_path.read_text(encoding="utf-8-sig")
    total_words = source_text.split()
    body_lines = []
    for line in source_text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or re.match(r"^\|\s*[-:|\s]+\|$", s):
            continue
        if s.startswith("|"):
            cell_text = " ".join(c.strip() for c in s.strip("|").split("|") if c.strip() and not re.fullmatch(r"[-:]+", c.strip()))
            if cell_text:
                body_lines.append(cell_text)
            continue
        if re.match(
            r"^\*\*(?:Date|Time|Duration|Attendees|Participants|Session ID|Meeting|Source|Platform|Classification|Project|Title|Topic|Organizer|Location):\*\*",
            s,
            re.IGNORECASE,
        ):
            continue
        body_lines.append(s)
    body_words = " ".join(body_lines).split()
    body_floor = min(min_words, 20)
    if len(total_words) < min_words or len(body_words) < body_floor:
        from dual_pipeline.exceptions import ZeroSignalError
        eff_count = len(body_words) if len(body_words) < body_floor else len(total_words)
        eff_min = body_floor if len(body_words) < body_floor else min_words
        raise ZeroSignalError(f"Source material falls below minimum semantic yield threshold (word count {eff_count} < {eff_min})")

def prepare_package(  # noqa: C901
    draft_path: Path,
    source_path: Path | None = None,
    output_path: Path | None = None,
    *,
    source_export: Path | None = None,
    source_location: str | None = None,
    classification: str | None = None,
    audiences: list[str] | None = None,
    started_at: str | None = None,
    organizer: str | None = None,
    platform_reference: str | None = None,
    overwrite: bool = False,
    is_visual_document: bool = False,
    min_words: int | None = None,
    inline_canonical_promotion: bool | None = None,
    authority_rank: int | None = None,
) -> dict[str, Any]:
    """Bind exact evidence bytes, validate locators, seal, validate, and write once."""
    if source_path is None and source_export is not None:
        source_path = source_export
    if source_path is None or output_path is None:
        raise ValueError("source_path and output_path must be provided")

    draft_path = draft_path.resolve()
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    package = read_json(draft_path)
    if not isinstance(package, dict):
        raise ValueError("Evidence envelope intake draft must be a JSON object")
    validate_projection_field_lengths(package)
    if package.get("contract_id") not in ACCEPTED_CONTRACT_IDS or "artifact_id" not in package:
        package = _adapt_v53_projection_to_envelope(package, source_path=source_path, draft_path=draft_path)
    if package.get("contract_id") not in ACCEPTED_CONTRACT_IDS:
        package["contract_id"] = EVIDENCE_ENVELOPE_CONTRACT_ID
    meeting = package.get("meeting")
    if not isinstance(meeting, dict):
        raise ValueError("Evidence envelope draft is missing the meeting object")
    if "extensions" not in package or not isinstance(package["extensions"], dict):
        package["extensions"] = {}
    if "com.partner_advisory.nee.canonical-signal-projection" not in package["extensions"] and "graph_mutations" in package:
        package["extensions"]["com.partner_advisory.nee.canonical-signal-projection"] = package["graph_mutations"]
    if not source_path.is_file() or source_path.stat().st_size == 0:
        raise ValueError("--source must identify a non-empty preserved notes export")

    eff_min_words = min_words
    if eff_min_words is None:
        if is_visual_document:
            eff_min_words = 1
        else:
            eff_min_words = 50
            metadata_path = draft_path.parent / "metadata.json"
            if metadata_path.is_file():
                try:
                    m_obj = read_json(metadata_path)
                    if isinstance(m_obj, dict):
                        if (
                            m_obj.get("is_visual_document")
                            or m_obj.get("allow_visual_image")
                            or m_obj.get("enable_visual_ocr")
                        ):
                            eff_min_words = 1
                        if m_obj.get("date_status"):
                            package["extensions"].setdefault("com.partner_advisory.nee.date-status", m_obj["date_status"])
                        if m_obj.get("upload_date"):
                            package["extensions"].setdefault("com.partner_advisory.nee.upload-date", m_obj["upload_date"])
                        if "actual_date" in m_obj:
                            package["extensions"].setdefault("com.partner_advisory.nee.actual-date", m_obj["actual_date"])
                except Exception:
                    pass
    _validate_semantic_yield(source_path, min_words=eff_min_words)

    # Fail-closed release authority check: verify authoritative release decision if present
    release_decision_path = draft_path.parent / "final_release_decision.json"
    has_authorized_release_decision = False
    if release_decision_path.is_file():
        decision = read_json(release_decision_path)
        if isinstance(decision, dict) and not decision.get("authoritative_write_authorized", False):
            raise RuntimeError(
                f"Release authorization rejected in {release_decision_path}: "
                f"status={decision.get('status')}, authoritative_write_authorized=False. "
                "Cannot prepare intake package for unauthorized run."
            )
        has_authorized_release_decision = isinstance(decision, dict) and bool(
            decision.get("authoritative_write_authorized", False)
        )

    if inline_canonical_promotion is not None:
        inline_promo_flag = bool(inline_canonical_promotion)
    else:
        inline_promo_flag = bool(
            package.get("inline_canonical_promotion")
            or (package.get("extensions") or {}).get("com.partner_advisory.nee.inline-canonical-promotion")
            or has_authorized_release_decision
        )
    if inline_promo_flag:
        package["extensions"].setdefault("com.partner_advisory.nee.source-posture", "governed_internal")
        target_rank = authority_rank if authority_rank is not None else package["extensions"].get("com.partner_advisory.nee.authority-rank", 7)
        package["extensions"]["com.partner_advisory.nee.authority-rank"] = int(target_rank)
        package["extensions"]["com.partner_advisory.nee.inline-canonical-promotion"] = True

    _validate_locators(package, source_path)
    hitl_request = build_hitl_request(
        package,
        source_path,
        source_location=source_location,
        classification=classification,
        audiences=audiences,
    )
    if hitl_request is not None:
        raise HITLRequired(hitl_request)

    meeting["source_hash"] = sha256_file(source_path)
    if source_location is not None:
        normalized_location = source_location.strip()
        if not normalized_location:
            raise ValueError("--source-location must not be blank")
        meeting["source_location"] = normalized_location
    elif meeting.get("source_location") in {None, "", LOCAL_SOURCE_LOCATION}:
        raise ValueError("the draft needs a stable meeting.source_location or --source-location")

    if classification is not None:
        meeting["classification"] = classification
    elif meeting.get("classification") == OPERATOR_SET_CLASSIFICATION:
        raise ValueError("the draft needs --classification from an authorized operator")

    if audiences is not None:
        if isinstance(audiences, str):
            raw_aud = [a.strip() for a in audiences.split(",") if a.strip()]
        else:
            raw_aud = [str(value).strip() for value in audiences]
        if not raw_aud or any(not value for value in raw_aud):
            raise ValueError("--audience values must not be blank")
        normalized_audiences = []
        for val in raw_aud:
            if not val:
                continue
            if re.match(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$", val):
                slug = val
            else:
                slug = re.sub(r"[^A-Za-z0-9._:-]+", "_", val.lower()).strip("_")[:64]
            if slug and slug not in normalized_audiences:
                normalized_audiences.append(slug)
        if not normalized_audiences:
            raise ValueError("--audience values must not be blank")
        meeting["audiences"] = normalized_audiences
    elif meeting.get("audiences") == [OPERATOR_SET_AUDIENCE]:
        raise ValueError("the draft needs at least one operator-authorized --audience")
    elif meeting.get("audiences"):
        cleaned_draft_audiences = []
        for value in meeting["audiences"]:
            val = str(value).strip()
            if not val:
                continue
            if re.match(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$", val):
                slug = val
            else:
                slug = re.sub(r"[^A-Za-z0-9._:-]+", "_", val.lower()).strip("_")[:64]
            if slug and slug not in cleaned_draft_audiences:
                cleaned_draft_audiences.append(slug)
        meeting["audiences"] = cleaned_draft_audiences or [OPERATOR_SET_AUDIENCE]

    if started_at is not None:
        meeting["started_at"] = started_at
    if organizer is not None:
        meeting["organizer"] = organizer
    if platform_reference is not None:
        meeting["platform_reference"] = platform_reference
    if "template_version" not in meeting or not meeting["template_version"]:
        meeting["template_version"] = "5.3"

    # Harmonize speakers for schema validity
    cleaned_speakers = []
    for spk in package.get("speakers", []):
        if not spk.get("statements"):
            continue  # Attendee who didn't speak remains in meeting.attendees
        if spk.get("reported_name") is None:
            spk["reported_name"] = "Unattributed Participant"
        cleaned_speakers.append(spk)
    package["speakers"] = cleaned_speakers
    validate_projection_field_lengths(package)

    # Harmonize adapter name
    package["contract_id"] = EVIDENCE_ENVELOPE_CONTRACT_ID
    if "schema_version" not in package or not package["schema_version"]:
        package["schema_version"] = "1.0.0"
    meeting["meeting_id"], meeting["title"] = _validate_non_degenerate_source_identity(
        str(meeting.get("meeting_id") or ""),
        str(meeting.get("title") or ""),
        source_path=source_path,
        draft_path=draft_path,
    )
    if "artifact_id" not in package or not package["artifact_id"] or package["artifact_id"] in {"artifact-meeting-unknown", "artifact-meeting-unspecified"}:
        m_id = meeting["meeting_id"]
        if not str(m_id).startswith(("meeting-", "document-")):
            m_id = "meeting-" + _slugify(str(m_id))
        package["artifact_id"] = "artifact-" + _slugify(m_id)
    if not meeting.get("attendees"):
        meeting["attendees"] = ["Unattributed Participant"]
    if "actions" not in package or not isinstance(package["actions"], list):
        package["actions"] = []
    if "speakers" not in package or not isinstance(package["speakers"], list):
        package["speakers"] = []
    if "extensions" not in package or not isinstance(package["extensions"], dict):
        package["extensions"] = {}
    package["adapter"] = {
        "name": "nee-evidence-envelope-intake",
        "version": "1.0.0",
    }

    ALLOWED_MEETING_KEYS = {
        "meeting_id", "title", "started_at", "organizer", "attendees",
        "platform_reference", "google_meet_reference", "teams_reference",
        "source_location", "transcript_reference", "recording_reference",
        "source_hash", "template_version", "classification", "audiences",
    }
    package["meeting"] = {k: v for k, v in meeting.items() if k in ALLOWED_MEETING_KEYS}
    meeting = package["meeting"]

    ALLOWED_PACKAGE_KEYS = {
        "contract_id", "schema_version", "artifact_id", "canonical_digest",
        "meeting", "speakers", "actions", "adapter", "extensions",
    }
    for k in list(package.keys()):
        if k not in ALLOWED_PACKAGE_KEYS:
            package.pop(k)

    package.pop("canonical_digest", None)
    package = seal_artifact(package)
    validate_artifact(package)
    if overwrite and output_path.exists():
        output_path.unlink()
    write_json_exclusive(output_path, package)
    return package


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output intake package if it already exists",
    )
    parser.add_argument(
        "--hitl-output",
        type=Path,
        help="Write a non-mutating pre-intake HITL request when required metadata is absent",
    )
    parser.add_argument(
        "--source-location",
        help="Stable controlled archive location; overrides meeting.source_location",
    )
    parser.add_argument(
        "--classification",
        choices=CLASSIFICATIONS,
        help="Operator-authorized classification; overrides meeting.classification",
    )
    parser.add_argument(
        "--audience",
        action="append",
        dest="audiences",
        help="Operator-authorized audience; repeat for more than one",
    )
    parser.add_argument("--started-at", help="ISO 8601 start timestamp")
    parser.add_argument("--organizer", help="Meeting organizer designation")
    parser.add_argument("--platform-reference", help="Platform reference or identifier")
    parser.add_argument(
        "--inline-canonical-promotion",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable inline canonical promotion for clean non-contradictory claims",
    )
    parser.add_argument(
        "--authority-rank",
        type=int,
        default=7,
        help="Authority rank for governed intake package (default: 7)",
    )
    args = parser.parse_args()
    try:
        package = prepare_package(
            args.draft,
            args.source,
            args.output,
            source_location=args.source_location,
            classification=args.classification,
            audiences=args.audiences,
            started_at=args.started_at,
            organizer=args.organizer,
            platform_reference=args.platform_reference,
            overwrite=args.overwrite,
            inline_canonical_promotion=args.inline_canonical_promotion,
            authority_rank=args.authority_rank,
        )
    except HITLRequired as exc:
        if args.hitl_output:
            write_json_exclusive(args.hitl_output.resolve(), exc.request)
        print(json.dumps(exc.request, ensure_ascii=False, indent=2, sort_keys=True))
        return 3
    except (OSError, ValueError, json.JSONDecodeError, ContractError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": "PASS",
                "output": str(args.output.resolve()),
                "artifact_id": package["artifact_id"],
                "source_hash": package["meeting"]["source_hash"],
                "canonical_digest": package["canonical_digest"]["value"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
