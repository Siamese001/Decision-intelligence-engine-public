"""
Dedicated parser for completed Deliverable 01 (`01_*_hitl_resolved_transcript.md`) inputs.

Extracts header metadata (`**Date:**`, `**Meeting:**`, `**Attendees:**`), Attribution Summary
table speaker/role mappings, `[OPERATOR VERIFIED]` Ambiguity Lock Register entries, and
counts dialogue turns across both single-line (`**[HH:MM:SS] Name:**`) and multi-line
(`### [HH:MM:SS]\\n\\n**Name:**`) Deliverable 01 formats without scraping ALR/Summary tables
as fake attendees.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_TURN_LINE_RE = re.compile(
    r"^(?:\*\*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*[^*:]+:\*\*|"
    r"\[\d{1,2}:\d{2}(?::\d{2})?\]\s*[^:]+:|"
    r"\*\*(?!Date|Time|Duration|Platform|Project|Classification|Meeting|Attendees|Source|Diarization|Speaker\s+Attribution)[A-Za-z][A-Za-z0-9\s\.\-']+:\*\*)",
    re.MULTILINE,
)

_HEADER_TURN_RE = re.compile(
    r"^###\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*\n+\s*\*\*([^*\n:]+?):?\*\*",
    re.MULTILINE,
)

_NON_ATTENDEE_TABLE_HEADERS = (
    "ambiguity lock",
    "attribution summary",
    "lock id",
    "acoustic cluster",
    "evidence basis",
    "resolution status",
    "confidence",
    "turn count",
    "word share",
)


def is_completed_output_01_path(path_or_str: Any) -> bool:
    """Returns True if the given path points to a completed `01_*_hitl_resolved_transcript.md`."""
    if not path_or_str:
        return False
    p = Path(str(path_or_str))
    name = p.name.lower()
    return (
        name.startswith("01_")
        and "hitl_resolved_transcript" in name
        and name.endswith(".md")
    )


def is_completed_output_01_text(text: str) -> bool:
    """Returns True if the markdown text has the structural markers of a completed Deliverable 01."""
    if not text or len(text) < 100:
        return False
    head = text[:2500]
    has_header = (
        "# Forensic Normalizer" in head
        or "HITL Resolved Transcript" in head
        or "## Attribution Summary" in text
        or "## Ambiguity Lock Register" in text
    )
    return has_header and count_output_01_turns(text) >= 1


def _is_inline_turn_line(s: str) -> bool:
    if (
        s.startswith("**[")
        or s.startswith("[0")
        or s.startswith("[1")
        or s.startswith("**Speaker")
    ) and ":" in s:
        return True
    return bool(
        s.startswith("**")
        and ":**" in s
        and not any(
            s.lower().startswith(f"**{k}")
            for k in (
                "date",
                "time",
                "duration",
                "platform",
                "project",
                "classification",
                "meeting",
                "attendees",
                "source",
                "diarization",
                "speaker attribution",
            )
        )
    )


def count_output_01_turns(text: str) -> int:
    """Counts speaker turns across both inline and `### [HH:MM:SS]\\n\\n**Name:**` formats."""
    if not text:
        return 0
    header_turns = len(_HEADER_TURN_RE.findall(text))
    inline_turns = sum(1 for line in text.splitlines() if _is_inline_turn_line(line.strip()))
    return max(header_turns, inline_turns)


def _parse_header_metadata(text: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    head = text[:4000]
    for key, target in (
        ("Date", "date"),
        ("Time", "time"),
        ("Meeting", "meeting_name"),
        ("Project", "project"),
        ("Platform", "platform"),
        ("Classification", "classification"),
    ):
        m = re.search(rf"\*\*{key}:\*\*\s*([^\n]+)", head, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val:
                meta[target] = val
    return meta


def _parse_attribution_summary_roles(text: str) -> Dict[str, Dict[str, str]]:
    """Extracts resolved speaker names and roles from `## Attribution Summary` without scraping ALR rows."""
    roles: Dict[str, Dict[str, str]] = {}
    sec_match = re.search(
        r"^##+\s*Attribution Summary\b(.*?)(?=^##+\s|\Z)",
        text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    if not sec_match:
        return roles
    block = sec_match.group(1)
    header_cols: List[str] = []
    for line in block.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells or all(set(c) <= {"-", ":", " "} for c in cells):
            continue
        lower_cells = [c.lower() for c in cells]
        if not header_cols:
            header_cols = lower_cells
            continue
        name_idx = next(
            (
                i
                for i, c in enumerate(header_cols)
                if "resolved" in c or "speaker" in c or "attendee" in c or "name" in c
            ),
            0,
        )
        role_idx = next(
            (i for i, c in enumerate(header_cols) if "role" in c or "title" in c),
            -1,
        )
        cluster_idx = next(
            (i for i, c in enumerate(header_cols) if "cluster" in c or "raw" in c or "voice" in c),
            -1,
        )
        if name_idx < len(cells):
            raw_name = re.sub(r"\*\*|`", "", cells[name_idx]).strip()
            name = re.sub(r"\s*\(.*?\)", "", raw_name).strip()
            if not name or name.lower() in ("---", "none", "n/a", "unattributed"):
                continue
            role = (
                re.sub(r"\*\*|`", "", cells[role_idx]).strip()
                if 0 <= role_idx < len(cells)
                else ""
            )
            cluster = (
                re.sub(r"\*\*|`", "", cells[cluster_idx]).strip()
                if 0 <= cluster_idx < len(cells)
                else ""
            )
            roles[name] = {"name": name, "role": role, "cluster": cluster}
    return roles


def _parse_ambiguity_lock_register(text: str) -> List[Dict[str, str]]:
    """Extracts `[OPERATOR VERIFIED]` rows from `## Ambiguity Lock Register`."""
    locks: List[Dict[str, str]] = []
    sec_match = re.search(
        r"^##+\s*Ambiguity Lock Register\b(.*?)(?=^##+\s|\Z)",
        text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    if not sec_match:
        return locks
    block = sec_match.group(1)
    for line in block.splitlines():
        s = line.strip()
        if not s.startswith("|") or "---" in s:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 3 and "lock" not in cells[0].lower():
            locks.append(
                {
                    "lock_id": cells[0],
                    "cluster_or_span": cells[1],
                    "resolved_speaker": cells[2],
                    "status": cells[-1],
                    "operator_verified": "OPERATOR VERIFIED" in s.upper(),
                }
            )
    return locks


def parse_completed_output_01(
    text: str, metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Hydrates metadata from a completed Deliverable 01 markdown text.
    Populates `turn_count`, `diarization_status`, `speaker_map`, `roster`, and `alr_locks`.
    """
    parsed_meta = _parse_header_metadata(text)
    roles_map = _parse_attribution_summary_roles(text)
    alr_locks = _parse_ambiguity_lock_register(text)
    turn_count = count_output_01_turns(text)

    # Extract dialogue speakers from `### [HH:MM:SS]\n\n**Name:**` and `**[HH:MM:SS] Name:**`
    dialogue_speakers: List[str] = []
    for spk in _HEADER_TURN_RE.findall(text):
        clean = re.sub(r"\s*\(.*?\)", "", spk).strip()
        if clean and clean not in dialogue_speakers:
            dialogue_speakers.append(clean)
    for m in re.finditer(r"\*\*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*([^*:]+):\*\*", text):
        clean = re.sub(r"\s*\(.*?\)", "", m.group(1)).strip()
        if clean and clean not in dialogue_speakers:
            dialogue_speakers.append(clean)

    speaker_map: Dict[str, str] = {}
    for name, info in roles_map.items():
        cluster = info.get("cluster") or name
        speaker_map[cluster] = name
        speaker_map[name] = name
    for spk in dialogue_speakers:
        speaker_map.setdefault(spk, spk)

    result = {
        "header_metadata": parsed_meta,
        "attribution_roles": roles_map,
        "alr_locks": alr_locks,
        "turn_count": turn_count,
        "dialogue_speakers": dialogue_speakers,
        "speaker_map": speaker_map,
    }

    if metadata is not None:
        for k, v in parsed_meta.items():
            if not metadata.get(k):
                metadata[k] = v
        metadata["turn_count"] = max(int(metadata.get("turn_count") or 0), turn_count)
        metadata["diarization_status"] = "DIARIZED_ACOUSTIC_RESOLVED"
        metadata["freeze_output_1"] = True
        metadata["alr_locks"] = alr_locks
        if speaker_map and not metadata.get("speaker_map"):
            metadata["speaker_map"] = speaker_map
        if roles_map and not metadata.get("hitl_role_resolutions"):
            metadata["hitl_role_resolutions"] = {
                n: info["role"] for n, info in roles_map.items() if info.get("role")
            }

    return result


def _has_valid_passing_track1_outputs(input_val: Any, meta: Dict[str, Any]) -> bool:
    """Return True if valid, exit-QA-passing Outputs 02 and 03 already exist for this frozen Output 01 SHA-256."""
    if meta.get("force_rerun"):
        return False
    try:
        p = Path(str(input_val)).expanduser().resolve()
        if not p.is_file():
            return False
        out_p = p.parent
        dec_f = out_p / "final_release_decision.json"
        if not dec_f.is_file() or not list(out_p.glob("02_*_executive_recap.md")) or not list(out_p.glob("03_*_canonical_signal_projection.json")):
            return False
        import hashlib
        import json
        f1_sha = hashlib.sha256(p.read_bytes()).hexdigest()
        dec = json.loads(dec_f.read_text(encoding="utf-8"))
        if dec.get("status") != "PASS":
            return False
        recorded = {dec.get("input_hash"), dec.get("source_file_hash"), dec.get("output_01_sha256")}
        rcpt_f = out_p / "pipeline_execution_receipt.json"
        if rcpt_f.is_file():
            rcpt = json.loads(rcpt_f.read_text(encoding="utf-8"))
            recorded.add(rcpt.get("source_evidence", {}).get("raw_source_sha256"))
            recorded.add(rcpt.get("stages", {}).get("U0", {}).get("correlation", {}).get("input_digest"))
        return f1_sha in (recorded - {None, ""})
    except Exception:
        return False


def apply_track1_track2_semantic_defaults(
    meta: Dict[str, Any], input_val: Any, is_doc: bool, is_notes: bool, is_out3: bool
) -> None:
    """Applies Track 1 (`01_*_hitl_resolved_transcript.md`) and Track 2 (`--output3-only`) flags."""
    if is_completed_output_01_path(input_val) and not is_doc and not is_notes:
        meta["freeze_output_1"] = True
        meta["resume"] = True
        if not meta.get("regenerate") and not is_out3:
            if not _has_valid_passing_track1_outputs(input_val, meta):
                meta["regenerate"] = "02,03"
                meta["outputs"] = ["02", "03"]
                meta["auto_regenerate_track1"] = True
            else:
                meta["auto_regenerate_track1"] = False
    if is_out3:
        meta.update({"output3_only": True, "outputs": ["03"], "skip_output_1": True, "skip_output_2": True})
        if not is_doc and not meta.get("is_document") and not meta.get("regenerate"):
            meta["regenerate"] = "03"
            meta["resume"] = True


def extract_non_alr_table_attendees(raw_transcript: str) -> set[str]:
    """Extracts attendee names from markdown tables while skipping ALR and Attribution Summary tables."""
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import is_blacklisted_attendee
    speakers: set[str] = set()
    in_table = False
    current_section = ""
    for line in raw_transcript.splitlines():
        s = line.strip()
        if s.startswith("#"):
            current_section = s.lower()
            in_table = False
            continue
        if not s.startswith("|"):
            in_table = False
            continue
        if any(sec in current_section for sec in ("ambiguity lock", "attribution summary", "audit", "governance")):
            continue
        m = re.match(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if not m:
            continue
        c1, c2, c3 = m.group(1).lower(), m.group(2).lower(), m.group(3).lower()
        row_lower = f"{c1} | {c2} | {c3}"
        if any(h in row_lower for h in _NON_ATTENDEE_TABLE_HEADERS):
            in_table = False
            continue
        if any(k in c for k in ("speaker", "attendee", "participant") for c in (c1, c2, c3)):
            in_table = True
        elif in_table and not is_blacklisted_attendee(m.group(2)):
            c2_clean = re.sub(r"\(.*?\)", "", m.group(2)).strip()
            if c2_clean.lower() not in ("standardized speaker name", "display name in teams", "---", ":---") and not c2_clean.startswith("["):
                speakers.add(c2_clean)
    return speakers
