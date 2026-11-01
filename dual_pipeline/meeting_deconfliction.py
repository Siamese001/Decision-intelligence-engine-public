"""
dual_pipeline/meeting_deconfliction.py

Governed Pre-Flight Meeting Deconfliction & Deduplication Engine.
Detects near-duplicate or temporally overlapping meetings on the same date/project
before expensive LLM synthesis, graph mutations, or duplicate directory creation.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple

from dual_pipeline.core import ROOT
from dual_pipeline.meeting_paths import normalize_meeting_date, sanitize_filename_slug

logger = logging.getLogger(__name__)

COLLISION_CONFIDENCE_THRESHOLD = 0.65


class MeetingDeconflictionError(RuntimeError):
    """Raised when a candidate meeting collides with an existing meeting on the same date."""

    def __init__(self, result: "MeetingCollisionResult") -> None:
        self.result = result
        msg = (
            f"[DUPLICATE_MEETING_COLLISION] Candidate meeting '{result.candidate_slug}' on {result.date_display} "
            f"collides with existing meeting '{result.existing_slug}' "
            f"(Collision Confidence: {result.confidence * 100:.1f}% >= {COLLISION_CONFIDENCE_THRESHOLD * 100:.0f}%).\n"
            f"Reasons for collision:\n"
            + "\n".join(f"  - {r}" for r in result.reasons)
            + "\n\nIngestion blocked to prevent duplicate meeting containers and graph state divergence.\n"
            "To override, pass --allow-duplicate with --collision-override-rationale '<rationale>', "
            "or use --augment-existing to merge into the existing meeting container."
        )
        super().__init__(msg)


@dataclass
class MeetingCollisionResult:
    """Detailed evaluation of collision between candidate and existing meeting."""
    is_collision: bool
    confidence: float
    candidate_slug: str
    date_display: str
    existing_slug: Optional[str] = None
    existing_path: Optional[Path] = None
    temporal_score: float = 0.0
    discourse_score: float = 0.0
    attendee_score: float = 0.0
    reasons: List[str] = field(default_factory=list)


def parse_time_window(time_str: Optional[str]) -> Tuple[Optional[time], Optional[time]]:
    """Parse start and end time from alphanumeric or formatted time strings."""
    if not time_str:
        return None, None
    raw = str(time_str).strip()
    # Pattern 1: HHMM[AP]M_HHMM[AP]M_ET (e.g. 1018AM_1030AM_ET, 0900AM_1000AM_ET)
    m1 = re.match(r"(?i)^(\d{1,2})(\d{2})([AP]M)_(\d{1,2})(\d{2})([AP]M)", raw)
    if m1:
        h1, m1_val, p1, h2, m2_val, p2 = m1.groups()
        t1 = _to_24h_time(int(h1), int(m1_val), p1.upper())
        t2 = _to_24h_time(int(h2), int(m2_val), p2.upper())
        return t1, t2

    # Pattern 2: HH:MM AM - HH:MM PM ET
    m2 = re.search(
        r"(?i)(\d{1,2}):(\d{2})\s*([AP]M)?\s*[-–]\s*(\d{1,2}):(\d{2})\s*([AP]M)",
        raw,
    )
    if m2:
        h1, min1, p1, h2, min2, p2 = m2.groups()
        p1 = p1.upper() if p1 else p2.upper()
        p2 = p2.upper()
        t1 = _to_24h_time(int(h1), int(min1), p1)
        t2 = _to_24h_time(int(h2), int(min2), p2)
        return t1, t2

    # Pattern 3: Single time marker
    m3 = re.search(r"(?i)(\d{1,2}):?(\d{2})?\s*([AP]M)", raw)
    if m3:
        h, min_val, p = m3.groups()
        t = _to_24h_time(int(h), int(min_val or "0"), p.upper())
        return t, t

    return None, None


def _to_24h_time(hour: int, minute: int, period: str) -> time:
    if period == "PM" and hour < 12:
        hour += 12
    elif period == "AM" and hour == 12:
        hour = 0
    return time(hour=max(0, min(23, hour)), minute=max(0, min(59, minute)))


def compute_temporal_overlap(
    cand_window: Tuple[Optional[time], Optional[time]],
    exist_window: Tuple[Optional[time], Optional[time]],
) -> Tuple[float, Optional[str]]:
    """Compute temporal overlap score (0.0 to 1.0) and descriptive reason."""
    c_start, c_end = cand_window
    e_start, e_end = exist_window
    if not (c_start and e_start):
        return 0.0, None

    c1 = c_start.hour * 60 + c_start.minute
    c2 = (c_end or c_start).hour * 60 + (c_end or c_start).minute
    e1 = e_start.hour * 60 + e_start.minute
    e2 = (e_end or e_start).hour * 60 + (e_end or e_start).minute

    if c2 < c1:
        c2 = c1 + 30
    if e2 < e1:
        e2 = e1 + 30

    overlap_start = max(c1, e1)
    overlap_end = min(c2, e2)
    overlap_mins = overlap_end - overlap_start

    if overlap_mins > 0:
        score = min(1.0, 0.70 + (overlap_mins / max(1, min(c2 - c1, e2 - e1))) * 0.30)
        return score, f"Direct temporal overlap of ~{overlap_mins}m between candidate and existing meeting"

    gap = min(abs(c1 - e2), abs(e1 - c2))
    if gap <= 15:
        score = 0.50 * (1.0 - (gap / 15.0))
        return score, f"Adjacent temporal proximity within {gap}m on the same date"

    return 0.0, None


def extract_discourse_fingerprint(text: str) -> Set[str]:
    """Extract named entities, monetary amounts, and high-signal lexical tokens."""
    if not text:
        return set()
    tokens: Set[str] = set()
    # 1. Monetary tokens (e.g. $12M, $12,000,000, $450k)
    for m in re.findall(r"\$\d+(?:,\d{3})*(?:\.\d+)?(?:[kmbt])?\b", text, re.IGNORECASE):
        tokens.add(m.lower().replace(",", ""))

    # 2. Named entity patterns (Capitalized words, 2-3 word phrases on same line)
    entities = re.findall(r"\b[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2}\b", text)
    stopwords = {
        "The", "This", "That", "Next", "Energy", "Project", "Meeting", "PartnerAdvisory",
        "Subject", "Key", "Discussion", "Points", "Findings", "Action", "Items",
        "Next Steps", "Open Decisions", "Table", "Summary", "Duration", "Header",
        "Date", "Speaker", "Objective", "Meeting Objective", "Hello",
    }
    for e in entities:
        if e not in stopwords and not any(sw == e for sw in stopwords):
            tokens.add(e.lower())

    # 3. High-signal technical / business domain keywords
    keywords = {
        "reid", "traci", "justin", "amit", "dan", "sharon", "chris", "deanna",
        "raisa", "weiser", "martinello", "shantae", "contractor", "contractors",
        "endorsement", "governance", "labor", "charter", "go/no-go", "savings",
        "capital", "third-party", "automation", "permitting", "developer",
    }
    sample_lower = text.lower()
    for kw in keywords:
        if kw in sample_lower:
            tokens.add(kw)

    return tokens


def compute_discourse_similarity(text_a: str, text_b: str) -> Tuple[float, List[str]]:
    """Compute token Jaccard and containment similarity between two discourses."""
    set_a = extract_discourse_fingerprint(text_a)
    set_b = extract_discourse_fingerprint(text_b)
    if not set_a or not set_b:
        return 0.0, []

    intersection = set_a & set_b
    if len(intersection) < 3:
        # Require at least 3 distinct shared tokens to avoid false collisions on sparse boilerplate
        return 0.0, []

    jaccard = len(intersection) / len(set_a | set_b)
    containment = len(intersection) / min(len(set_a), len(set_b))

    score = 0.40 * jaccard + 0.60 * containment
    reasons: List[str] = []
    if score >= 0.40:
        sample_shared = sorted(list(intersection))[:8]
        reasons.append(
            f"High discourse and entity similarity ({score * 100:.1f}%); "
            f"shared tokens: {', '.join(sample_shared)}"
        )
    return score, reasons


def _flatten_attendee_inputs(attendees: Any) -> List[str]:
    if isinstance(attendees, dict):
        out: List[str] = []
        for v in attendees.values():
            out.extend([str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else []))
        return out
    if isinstance(attendees, (list, set, tuple)):
        return [str(x) for x in attendees]
    return [str(attendees)] if attendees else []


def extract_attendee_names(attendees: Any) -> Set[str]:
    """Extract individual name tokens (lowercase) from attendee strings, lists, or dicts."""
    stopwords = {
        "and", "or", "the", "partner_advisory", "apex_global", "energy", "apex", "lead", "architect",
        "director", "product", "manager", "delivery", "principal", "consultant",
        "solution", "quantitative", "strategy", "analyst", "engineer", "nea",
    }
    names: Set[str] = set()
    for s in _flatten_attendee_inputs(attendees):
        s_clean = re.sub(r"[*_\-#:>]", " ", re.sub(r"\([^)]*\)", "", s))
        for token in re.findall(r"\b[A-Za-z]{3,}\b", s_clean):
            if token.lower() not in stopwords:
                names.add(token.lower())
    return names


def compute_attendee_overlap(att_a: Any, att_b: Any) -> Tuple[float, Optional[str]]:
    """Compute attendee roster overlap score."""
    names_a = extract_attendee_names(att_a)
    names_b = extract_attendee_names(att_b)
    if not names_a or not names_b:
        return 0.0, None

    intersection = names_a & names_b
    if not intersection:
        return 0.0, None

    jaccard = len(intersection) / len(names_a | names_b)
    containment = len(intersection) / min(len(names_a), len(names_b))
    score = 0.50 * jaccard + 0.50 * containment
    reason = f"Shared counterpart attendee(s): {', '.join(sorted(intersection)).title()}"
    return score, reason


def _read_meta_context(meta_file: Path) -> Tuple[Optional[str], Any]:
    if not meta_file.exists():
        return None, None
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        return data.get("time") or data.get("time_filename"), data.get("attendees") or data.get("roster")
    except Exception:
        return None, None


def _parse_md_file(path: Path, cur_time: Optional[str], cur_atts: Any, texts: List[str]) -> Tuple[Optional[str], Any]:
    try:
        t = path.read_text(encoding="utf-8")
        texts.append(t[:15000])
        if not cur_time:
            tm = re.search(r"\*\*Time:\*\*\s*([^\n]+)", t)
            if tm:
                cur_time = tm.group(1).strip()
        if not cur_atts:
            am = re.search(r"\*\*Attendees:\*\*\s*(.+?)(?=\n\s*(?:---|##|\*\*Time|\Z))", t, re.DOTALL)
            if am:
                cur_atts = am.group(1).strip()
    except Exception:
        pass
    return cur_time, cur_atts


def _read_receipt_fields(rf: Path) -> Tuple[Optional[str], Any]:
    try:
        rdata = json.loads(rf.read_text(encoding="utf-8"))
        return (
            rdata.get("time_window") or rdata.get("time") or rdata.get("meeting_time"),
            rdata.get("attendees") or rdata.get("expected_attendees"),
        )
    except Exception:
        return None, None


def _read_sibling_receipts(sibling_dir: Path, t_str: Optional[str], atts: Any) -> Tuple[Optional[str], Any]:
    if t_str and atts:
        return t_str, atts
    for name in ("record_start_receipt.json", "session_manifest.json"):
        rf = sibling_dir / name
        if rf.is_file():
            rt_str, ratts = _read_receipt_fields(rf)
            t_str = t_str or rt_str
            atts = atts or ratts
            if t_str and atts:
                break
    return t_str, atts


def _find_target_date_dirs(base_proj: Path, date_dir: str) -> List[Path]:
    target_dirs: List[Path] = []
    if (base_proj / date_dir).is_dir():
        target_dirs.append(base_proj / date_dir)
    if not base_proj.is_dir():
        return target_dirs
    for child in base_proj.iterdir():
        if child.is_dir() and not child.name.startswith(".") and (child / date_dir).is_dir():
            target_dirs.append(child / date_dir)
    return target_dirs


class MeetingCollisionDetector:
    """Evaluates candidate meetings against existing meetings on the same date."""

    def __init__(self, repo_root: Optional[Path] = None) -> None:
        self.root = repo_root or ROOT

    def _evaluate_sibling_candidate(
        self, item: Path, cand_slug: str, cand_win: Any,
        cand_att: Any, cand_text: Optional[str], date_disp: str,
    ) -> MeetingCollisionResult:
        sib_time, sib_att, sib_text = self._read_sibling_context(item)
        temp_score, temp_r = compute_temporal_overlap(cand_win, parse_time_window(sib_time))
        disc_score, disc_r = compute_discourse_similarity(cand_text or "", sib_text)
        att_score, att_r = compute_attendee_overlap(cand_att, sib_att)

        reasons = [r for r in [temp_r] + disc_r + [att_r] if r]
        conf = 0.40 * temp_score + 0.45 * disc_score + 0.15 * att_score
        if temp_score >= 0.50 and disc_score >= 0.50:
            conf = max(conf, 0.70 + 0.25 * disc_score)
        if disc_score >= 0.70:
            conf = max(conf, disc_score)
        if temp_score >= 0.85 and att_score >= 0.50:
            conf = max(conf, 0.80)

        return MeetingCollisionResult(
            is_collision=conf >= COLLISION_CONFIDENCE_THRESHOLD,
            confidence=round(conf, 3), candidate_slug=cand_slug, date_display=date_disp,
            existing_slug=item.name, existing_path=item, temporal_score=round(temp_score, 3),
            discourse_score=round(disc_score, 3), attendee_score=round(att_score, 3), reasons=reasons,
        )

    def check_candidate_collision(
        self, project: str, date_str: str, candidate_slug: str,
        candidate_time: Optional[str] = None, candidate_attendees: Optional[Any] = None,
        candidate_text: Optional[str] = None,
    ) -> MeetingCollisionResult:
        """Scan sibling meeting directories for duplicate collisions."""
        date_dir, date_disp = normalize_meeting_date(date_str)
        cand_slug = sanitize_filename_slug(candidate_slug)
        cand_win = parse_time_window(candidate_time)

        base_proj = self.root / "output" / project
        target_dirs = _find_target_date_dirs(base_proj, date_dir)
        if not target_dirs:
            return MeetingCollisionResult(False, 0.0, cand_slug, date_disp)

        best = self._scan_target_dirs_for_collision(
            target_dirs, cand_slug, cand_win, candidate_attendees, candidate_text, date_disp
        )
        return best or MeetingCollisionResult(False, 0.0, cand_slug, date_disp)

    def _scan_target_dirs_for_collision(
        self, target_dirs: List[Path], cand_slug: str, cand_win: Any,
        candidate_attendees: Any, candidate_text: Optional[str], date_disp: str,
    ) -> Optional[MeetingCollisionResult]:
        best: Optional[MeetingCollisionResult] = None
        for p_dir in target_dirs:
            for item in sorted(p_dir.iterdir()):
                if not item.is_dir() or item.name.startswith(".") or item.name == cand_slug:
                    continue
                res = self._evaluate_sibling_candidate(
                    item, cand_slug, cand_win, candidate_attendees, candidate_text, date_disp
                )
                if res.is_collision and (best is None or res.confidence > best.confidence):
                    best = res
        return best

    def _read_sibling_context(self, sibling_dir: Path) -> Tuple[Optional[str], Any, str]:
        """Extract time, attendees, and discourse text from sibling meeting files."""
        t_str, atts = _read_meta_context(sibling_dir / "metadata.json")
        t_str, atts = _read_sibling_receipts(sibling_dir, t_str, atts)
        texts: List[str] = []
        for glob_pat in (
            "*_live_transcript.md",
            "01_*_hitl_resolved_transcript.md",
            "01_*_Transcript.md",
            "02_*_executive_recap.md",
            "02_*_Recap.md",
        ):
            for f in sibling_dir.glob(glob_pat):
                t_str, atts = _parse_md_file(f, t_str, atts, texts)
        return t_str, atts, "\n".join(texts)
