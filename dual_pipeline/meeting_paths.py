"""dual_pipeline/meeting_paths.py

Single Source of Truth (SSOT) for meeting paths, directory structures,
and deliverable naming conventions across the Decision Intelligence Engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Union

from dual_pipeline.core import ROOT
from dual_pipeline.parameters import get_known_projects

DATE_REGEX = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-(19|20)\d{2}$")


def normalize_meeting_date(raw_date: Optional[Union[str, date, datetime]] = None) -> tuple[str, str]:
    """Normalizes any date input into (date_dirname MM-DD-YYYY, date_display MM/DD/YYYY)."""
    if raw_date is None or isinstance(raw_date, (datetime, date)):
        now = datetime.now() if raw_date is None else raw_date
        return now.strftime("%m-%d-%Y"), now.strftime("%m/%d/%Y")

    clean = raw_date.strip().replace("/", "-").replace(".", "-")
    parts = clean.split("-")
    if len(parts) == 3:
        if len(parts[0]) == 4 and len(parts[1]) <= 2 and len(parts[2]) <= 2:
            y, m, d = parts[0], parts[1].zfill(2), parts[2].zfill(2)
        else:
            m, d, y = parts[0].zfill(2), parts[1].zfill(2), parts[2]
        date_dir, date_disp = f"{m}-{d}-{y}", f"{m}/{d}/{y}"
        if not DATE_REGEX.match(date_dir):
            raise ValueError(f"Normalized date '{date_dir}' does not match MM-DD-YYYY format.")
        return date_dir, date_disp
    raise ValueError(f"Cannot parse meeting date from '{raw_date}'. Expected MM-DD-YYYY, MM/DD/YYYY, or ISO-8601.")


def sanitize_filename_slug(name: str) -> str:
    """Sanitizes meeting names for safe, deterministic filenames."""
    slug = re.sub(r"[^\w\-]+", "_", name.strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "Meeting"


def _parse_raw_attendee_item(item: Any, names: list[str]) -> None:
    if isinstance(item, str) and item.strip():
        names.append(item.strip())
    elif isinstance(item, dict) and "name" in item:
        name_str = str(item["name"]).strip()
        if "partner_advisory" in str(item.get("org", "")).lower() and "partner_advisory" not in name_str.lower():
            name_str = f"{name_str} (PartnerAdvisory)"
        names.append(name_str)


def _parse_attendee_string_val(raw_str: str) -> list[str]:
    raw = raw_str.strip()
    if raw.startswith(("{", "[")):
        try:
            import json
            return _parse_attendee_strings(json.loads(raw))
        except Exception:
            pass
    return [p.strip() for p in re.split(r"[,;&\n]+", raw) if p.strip()]


def _parse_attendee_dict(attendees: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for org, val in attendees.items():
        is_sl = "partner_advisory" in str(org).lower()
        for item in (val if isinstance(val, list) else [val]):
            b_len = len(names)
            _parse_raw_attendee_item(item, names)
            if is_sl and len(names) > b_len and "partner_advisory" not in names[-1].lower():
                names[-1] = f"{names[-1]} (PartnerAdvisory)"
    return names


def _parse_attendee_strings(attendees: Any) -> list[str]:
    if not attendees:
        return []
    if isinstance(attendees, str):
        return _parse_attendee_string_val(attendees)
    if isinstance(attendees, dict):
        return _parse_attendee_dict(attendees)
    if isinstance(attendees, list):
        names: list[str] = []
        for item in attendees:
            _parse_raw_attendee_item(item, names)
        return names
    return []


def _partition_attendees(names: list[str]) -> tuple[list[str], list[str], bool]:
    has_partner_advisory_mention = any("partner_advisory" in n.lower() for n in names)
    partner_advisory_members = {"jen pan", "jen", "amit ayer", "amit", "alex sanchez", "alex", "stephan", "dan", "partner_advisory", "partner_advisory team"}
    client_names, partner_advisory_names = [], []
    for n in names:
        nl = n.lower()
        if "partner_advisory" in nl:
            has_partner_advisory_mention = True
        first = n.split()[0].capitalize()
        if "partner_advisory" in nl or any(sm in nl for sm in partner_advisory_members):
            if first not in partner_advisory_names:
                partner_advisory_names.append(first)
        elif first not in client_names:
            client_names.append(first)
    return client_names, partner_advisory_names, has_partner_advisory_mention


def _build_attendee_slug_from_groups(
    client_names: list[str],
    partner_advisory_names: list[str],
    has_partner_advisory_mention: bool,
    names: list[str],
) -> str:
    if client_names and (has_partner_advisory_mention or len(partner_advisory_names) >= 2):
        return f"{client_names[0]}_PartnerAdvisory"
    if client_names and len(client_names) == 1 and len(partner_advisory_names) == 1:
        return f"{client_names[0]}_{partner_advisory_names[0]}"
    firsts = [n.split()[0].capitalize() for n in (client_names or names) if n.split()]
    return "_".join(firsts[:2]) if firsts else "Meeting"


def _extract_attendee_slug(attendees: Optional[Union[str, list[Any], dict[str, Any]]]) -> str:
    if not attendees:
        return ""
    names = _parse_attendee_strings(attendees)
    if not names:
        return ""
    client_names, partner_advisory_names, has_partner_advisory = _partition_attendees(names)
    return _build_attendee_slug_from_groups(client_names, partner_advisory_names, has_partner_advisory, names)


def _match_known_scope(norm: str, raw: str) -> Optional[str]:
    if "discuss" in norm and "sow" in norm:
        return "SOW_Boundaries"
    if ("designs" in norm or "design" in norm) and ("phase 1" in norm or "phase1" in norm or "phase_1" in raw.lower()):
        return "Phase1_Designs"
    if "sow" in norm and "observability" in norm:
        return "SOW1_Observability"
    return None


def _format_scope_word(w: str) -> str:
    if re.match(r"(?i)^phase\d+$", w):
        return w.capitalize()
    if re.match(r"(?i)^sow\d+$", w):
        return w.upper()
    return w.capitalize()


def _filter_scope_words(words: list[str], project_name: Optional[str]) -> list[str]:
    stop_words = {
        "discuss", "discussion", "review", "catchup", "catch", "up", "sync", "meeting", "call", "project",
        "lens", "nexus", "completion", "status", "weekly", "re", "fwd", "fw", "on", "for", "with", "about",
        "in", "at", "to", "from", "of", "the", "a", "an", "and", "by", "alignment", "team",
    }
    proj_tokens = {p for p in re.sub(r"[^a-z0-9]+", " ", (project_name or "").lower()).split() if len(p) >= 3 and p not in ("project", "eng", "team")}
    non_stop = [w for w in words if w.lower() not in stop_words]
    if not non_stop:
        return ["Sync"]
    if proj_tokens and len(non_stop) > 1:
        without = [w for w in non_stop if w.lower() not in proj_tokens]
        return without if without else non_stop
    return non_stop


def _extract_scope_slug(name: str, project_name: Optional[str] = None) -> str:
    raw = name.strip()
    words = re.sub(r"[^a-zA-Z0-9]+", " ", raw).split()
    if not words:
        return "Meeting"
    known = _match_known_scope(" ".join(words).lower(), raw)
    if known:
        return known
    filtered = _filter_scope_words(words, project_name)
    capped = [_format_scope_word(w) for w in filtered]
    slug = "_".join(capped)
    slug = re.sub(r"(?i)\bphase_?(\d+)(?=[^0-9]|$)", r"Phase\1", slug)
    slug = re.sub(r"(?i)\bsow_?(\d+)(?=[^0-9]|$)", r"SOW\1", slug)
    return sanitize_filename_slug(slug) or "Meeting"


def _is_attendee_slug_redundant(attendee_slug: str, scope: str) -> bool:
    if not attendee_slug:
        return True
    att_lower, scope_lower = attendee_slug.lower(), scope.lower()
    if att_lower in scope_lower:
        return True
    att_tokens = [t for t in re.split(r"[^a-zA-Z0-9]+", att_lower) if t]
    scope_tokens = set(re.split(r"[^a-zA-Z0-9]+", scope_lower))
    if att_tokens and all(t in scope_tokens for t in att_tokens):
        return True
    if att_lower.endswith("partner_advisory") and scope_lower.endswith("partner_advisory"):
        non_partner_advisory = [t for t in att_tokens if t != "partner_advisory"]
        if non_partner_advisory and all(t in scope_tokens for t in non_partner_advisory):
            return True
    if att_tokens and any(t in scope_tokens for t in att_tokens):
        return True
    return False


def extract_slug_from_meeting_filename(filename: str) -> Optional[str]:
    """Extracts established meeting slug from canonical filenames (WAV, live transcript, segments)."""
    base = Path(filename).stem
    m = re.match(
        r"^(?:00|01)_\d{2}-\d{2}-\d{4}_(.+?)(?:_master(?:_\d+)?|_live_transcript|_segments)?$",
        base,
        re.IGNORECASE,
    )
    if m:
        cand = m.group(1).strip()
        if cand:
            return cand
    return None


def _has_attendee_in_scope(attendees: Any, scope: str) -> bool:
    """Checks if scope already contains any attendee from the roster."""
    if not attendees or not scope:
        return False
    names = _parse_attendee_strings(attendees)
    scope_tokens = set(re.split(r"[^a-zA-Z0-9]+", scope.lower()))
    stop_names = {"sync", "call", "team", "meet", "meeting", "project", "review", "will", "may"}
    all_att_firsts = {
        n.split()[0].lower() for n in names
        if n.split() and len(n.split()[0]) > 2 and n.split()[0].lower() not in stop_names
    }
    return bool(all_att_firsts and any(fn in scope_tokens for fn in all_att_firsts))


def standardize_meeting_slug(
    name: str,
    attendees: Optional[Union[str, list[Any], dict[str, Any]]] = None,
    max_len: int = 40,
    project_name: Optional[str] = None,
    initial_slug: Optional[str] = None,
) -> str:
    """Standardizes meeting names across all outputs to be concise (<=40 chars)."""
    if initial_slug:
        return sanitize_filename_slug(initial_slug)[:max_len]
    extracted = extract_slug_from_meeting_filename(name)
    if extracted:
        return sanitize_filename_slug(extracted)[:max_len]
    eff_name = name
    scope = _extract_scope_slug(eff_name, project_name=project_name)
    attendee_slug = _extract_attendee_slug(attendees)

    has_existing = _has_attendee_in_scope(attendees, scope)
    if attendee_slug and not has_existing and not _is_attendee_slug_redundant(attendee_slug, scope):
        combined = f"{scope}_{attendee_slug}"
        if len(combined) > max_len:
            max_s = max(max_len - len(attendee_slug) - 1, 8)
            trunc = scope[:max_s].rstrip("_")
            trunc = trunc.rsplit("_", 1)[0] if "_" in trunc else trunc
            combined = f"{trunc}_{attendee_slug}"
    else:
        combined = scope

    slug = sanitize_filename_slug(combined)
    if len(slug) > max_len:
        truncated = slug[:max_len].rstrip("_")
        slug = truncated if "_" in truncated else slug[:max_len]
    return slug or "Meeting"


def normalize_time_str(time_str: Optional[str] = None) -> str:
    """Normalizes time string into alphanumeric format e.g. 0900AM_1000AM_ET."""
    if not time_str:
        return datetime.now().strftime("%H%M%S")
    clean = re.sub(r"[\s\:\-\u2013]+", "_", time_str.strip())
    clean = re.sub(r"_+", "_", clean).strip("_")
    return clean or "0000"


def _validate_path_containment(out_dir: Path, c_dir: Path, root: Path) -> None:
    valid_out_roots = [(root / "output").resolve()] + [(p / "output").resolve() for p in root.parents if (p / "output").exists()]
    valid_cache_roots = [(root / "output" / "transcript_cache").resolve()] + [
        (p / "output" / "transcript_cache").resolve() for p in root.parents if (p / "output" / "transcript_cache").exists()
    ]
    if not any(out_dir.resolve().is_relative_to(vor) for vor in valid_out_roots):
        raise ValueError(f"Path containment violation: output_dir '{out_dir}' escapes output root.")
    if not any(c_dir.resolve().is_relative_to(vcr) for vcr in valid_cache_roots):
        raise ValueError(f"Path containment violation: cache_dir '{c_dir}' escapes cache root.")


def _resolve_clean_name(name: str, attendees: Any, project: str, standardize: bool, short: bool) -> str:
    clean = standardize_meeting_slug(name, attendees, project_name=project) if standardize else sanitize_filename_slug(name)
    p_prefix = f"{project.lower()}_"
    if short and clean.lower().startswith(p_prefix):
        clean = clean[len(p_prefix):] or clean
    return clean


def _build_meeting_deliverables(out_dir: Path, clean_name: str, stem: str, date_dir: str, short: bool) -> tuple[Path, Path, Path]:
    if short:
        return (out_dir / f"01_{clean_name}_{date_dir}_Transcript.md", out_dir / f"02_{clean_name}_{date_dir}_Recap.md", out_dir / f"03_{clean_name}_{date_dir}_Signals.json")
    return (out_dir / f"01_{stem}_hitl_resolved_transcript.md", out_dir / f"02_{stem}_executive_recap.md", out_dir / f"03_{stem}_canonical_signal_projection.json")


@dataclass(frozen=True)
class MeetingPathEnvelope:
    """Canonical path envelope for a meeting's deliverables, receipts, and scratch caches."""
    project_name: str; date_dirname: str; date_display: str; meeting_name: str; time_str: str; stem: str; output_dir: Path; cache_dir: Path
    deliverable_01: Path; deliverable_02: Path; deliverable_03: Path
    qa_report: Path; judge_evaluation: Path; release_decision: Path; intake_package: Path
    scratch_master_wav: Path; scratch_live_transcript: Path; scratch_segments_jsonl: Path; scratch_copilot_jsonl: Path
    scratch_session_manifest: Path; scratch_chunks_dir: Path
    subfolder: Optional[str] = None; category: Optional[str] = None

    @classmethod
    def resolve(
        cls,
        project_name: str,
        meeting_date: Optional[Union[str, date, datetime]] = None,
        meeting_name: str = "Meeting",
        meeting_time: Optional[str] = None,
        repo_root: Optional[Path] = None,
        subfolder: Optional[Union[str, bool]] = None,
        attendees: Optional[Any] = None,
        standardize_name: bool = False,
        category: Optional[str] = None,
        short_deliverables: bool = False,
    ) -> MeetingPathEnvelope:
        root = repo_root or ROOT
        norm_project = project_name.strip()
        known = get_known_projects()
        if norm_project not in known:
            raise ValueError(f"Unknown project identifier '{norm_project}'. Must be one of: {sorted(known)}")

        date_dirname, date_display = normalize_meeting_date(meeting_date)
        clean_name = _resolve_clean_name(meeting_name, attendees, norm_project, standardize_name, short_deliverables)
        clean_time = normalize_time_str(meeting_time)
        stem = f"{clean_name}_{date_dirname}_{clean_time}"

        subfolder_name: Optional[str] = None
        if isinstance(subfolder, str) and subfolder.strip():
            subfolder_name = sanitize_filename_slug(subfolder.strip())
        elif subfolder is True:
            subfolder_name = clean_name

        clean_cat = category.strip() if isinstance(category, str) and category.strip() else None
        base_out = root / "output" / norm_project
        base_cache = root / "output" / "transcript_cache" / norm_project
        if clean_cat:
            base_out, base_cache = base_out / clean_cat, base_cache / clean_cat

        out_dir = base_out / date_dirname / subfolder_name if subfolder_name else base_out / date_dirname
        c_dir = base_cache / date_dirname / subfolder_name if subfolder_name else base_cache / date_dirname

        _validate_path_containment(out_dir, c_dir, root)
        d01, d02, d03 = _build_meeting_deliverables(out_dir, clean_name, stem, date_dirname, short_deliverables)

        return cls(
            project_name=norm_project, date_dirname=date_dirname, date_display=date_display,
            meeting_name=meeting_name, time_str=clean_time, stem=stem, output_dir=out_dir, cache_dir=c_dir,
            deliverable_01=d01, deliverable_02=d02, deliverable_03=d03,
            qa_report=out_dir / "qa_gates_report.json", judge_evaluation=out_dir / "llm_judge_evaluation.json",
            release_decision=out_dir / "final_release_decision.json", intake_package=out_dir / "intake_package.json",
            scratch_master_wav=c_dir / f"00_{stem}_master.wav", scratch_live_transcript=c_dir / f"01_{stem}_live_transcript.md",
            scratch_segments_jsonl=c_dir / f"01_{stem}_segments.jsonl", scratch_copilot_jsonl=c_dir / f"01_{stem}_copilot_feedback.jsonl",
            scratch_session_manifest=c_dir / f"00_{stem}_manifest.json", scratch_chunks_dir=c_dir / ".chunks",
            subfolder=subfolder_name, category=clean_cat,
        )

    def ensure_output_dir(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir

    def ensure_cache_dir(self) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.scratch_chunks_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir

    def all_deliverables_exist(self) -> bool:
        return self.deliverable_01.is_file() and self.deliverable_02.is_file() and self.deliverable_03.is_file()

    def all_receipts_exist(self) -> bool:
        return all(p.is_file() for p in (self.qa_report, self.judge_evaluation, self.release_decision, self.intake_package))
