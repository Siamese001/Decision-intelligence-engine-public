"""Subtitle and structured JSON format parsers for transcript ingestion."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from dual_pipeline.transcript_pipeline_core.source_spans import SourceSpan, NormalizedTurn, NormalizedTranscript, merge_spans_into_turns

logger = logging.getLogger(__name__)

TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)"
)
VOICE_TAG_RE = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>|$)", re.DOTALL)


def format_timestamp(ts_raw: str) -> str:
    """Convert raw time string (MM:SS, H:MM:SS, HH:MM:SS, 10:15 AM, ISO-8601) into HH:MM:SS."""
    ts_raw = ts_raw.strip()
    iso_match = re.match(r"^\d{4}-\d{2}-\d{2}[T\s](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$", ts_raw, re.IGNORECASE)
    if iso_match:
        return f"{int(iso_match.group(1)):02d}:{int(iso_match.group(2)):02d}:{int(iso_match.group(3)):02d}"
    pt_match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$", ts_raw, re.IGNORECASE)
    if pt_match and any(pt_match.groups()):
        h, m, s = int(pt_match.group(1) or 0), int(pt_match.group(2) or 0), int(float(pt_match.group(3) or 0))
        return f"{h:02d}:{m:02d}:{s:02d}"
    am_pm_match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)$", ts_raw, re.IGNORECASE)
    if am_pm_match:
        h, m, s = int(am_pm_match.group(1)), int(am_pm_match.group(2)), int(am_pm_match.group(3) or 0)
        period = am_pm_match.group(4).upper()
        if period == "PM" and h < 12: h += 12
        elif period == "AM" and h == 12: h = 0
        return f"{h:02d}:{m:02d}:{s:02d}"

    ts_clean = re.sub(r"[.,]\d+$", "", ts_raw)
    parts = ts_clean.split(":")
    if len(parts) == 2:
        try:
            return f"00:{int(parts[0]):02d}:{int(parts[1]):02d}"
        except ValueError:
            return ts_clean
    elif len(parts) == 3:
        try:
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
        except ValueError:
            return ts_clean
    return ts_raw


def _clean_speaker_label_str(speaker: str) -> str:
    speaker = re.sub(r"(?:\s*[\u2013\u2014]\s*|\s+\-\s+).*$", "", speaker).strip()
    if "," in speaker:
        parts = [p.strip() for p in speaker.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            speaker = f"{parts[1]} {parts[0]}"
    return speaker.strip()


def _parse_vtt_line_speaker_and_text(line_clean: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract speaker and text from <v Speaker> tag or Speaker: prefix."""
    vm = VOICE_TAG_RE.search(line_clean)
    if vm:
        speaker = _clean_speaker_label_str(vm.group(1).strip())
        text = re.sub(r"<[^>]+>", "", vm.group(2)).strip()
        return speaker, text

    colon_m = re.match(r"^([A-Z][a-zA-Z0-9\s\-\u2013\u2014\.,\'’ʼ\(\)/]+?):\s*(.*)", line_clean)
    if colon_m and len(colon_m.group(1).split()) <= 6:
        speaker = _clean_speaker_label_str(colon_m.group(1).strip())
        text = re.sub(r"<[^>]+>", "", colon_m.group(2)).strip()
        return speaker, text

    return None, None


class _VttTurnBuilder:
    """Helper class to accumulate and segment subtitle speaker turns."""

    def __init__(self) -> None:
        self.spans: List[SourceSpan] = []
        self.curr_spk: Optional[str] = None
        self.curr_start_time: Optional[str] = None
        self.curr_end_time: Optional[str] = None
        self.curr_cue_id: Optional[str] = None
        self.curr_body: List[str] = []
        self.latest_start_time: str = "00:00:00"
        self.latest_end_time: Optional[str] = None

    def set_cue_time(self, raw_start: str, raw_end: Optional[str] = None) -> None:
        self.flush()
        self.latest_start_time = format_timestamp(raw_start)
        self.latest_end_time = format_timestamp(raw_end) if raw_end else None

    def add_speaker_line(self, speaker: str, text: Optional[str], cue_id: Optional[str] = None) -> None:
        if self.curr_spk and self.curr_spk != speaker:
            self.flush()

        self.curr_spk = speaker
        self.curr_start_time = self.curr_start_time or self.latest_start_time
        if self.latest_end_time:
            self.curr_end_time = self.latest_end_time
        if cue_id:
            self.curr_cue_id = self.curr_cue_id or cue_id
        if text:
            self.curr_body.append(text)

    def add_plain_text(self, raw_line: str, cue_id: Optional[str] = None) -> None:
        text = re.sub(r"<[^>]+>", "", raw_line).strip()
        if not text or text.isdigit():
            return
        self.curr_spk = self.curr_spk or "Unknown"
        self.curr_start_time = self.curr_start_time or self.latest_start_time
        if self.latest_end_time:
            self.curr_end_time = self.latest_end_time
        if cue_id:
            self.curr_cue_id = self.curr_cue_id or cue_id
        self.curr_body.append(text)

    def flush(self) -> None:
        if self.curr_spk and self.curr_body:
            self.spans.append(SourceSpan(
                start_time=self.curr_start_time or "00:00:00",
                end_time=self.curr_end_time,
                speaker_label=self.curr_spk if self.curr_spk != "Unknown" else None,
                text=" ".join(self.curr_body).strip(),
                cue_id=self.curr_cue_id
            ))
            # Reset only the text payload to allow consecutive accumulation, but preserve state if needed.
            # Actually, just reset all so we can parse the next properly.
            self.curr_spk = None
            self.curr_start_time = None
            self.curr_end_time = None
            self.curr_cue_id = None
            self.curr_body = []

def _to_sec(ts: str) -> float:
    parts = ts.replace(",", ".").split(":")
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    try:
        return float(ts)
    except ValueError:
        return 0.0


def _is_vtt_cue_id_line(lines: List[str], idx: int, line_clean: str) -> bool:
    if idx + 1 >= len(lines):
        return False
    return not TIME_RE.search(line_clean) and bool(TIME_RE.search(lines[idx + 1].strip()))


def _consume_vtt_time_line(
    line_clean: str,
    tm: re.Match[str],
    current_cue_id: Optional[str],
    raw_cues: List[Dict[str, Any]],
) -> Tuple[Optional[Tuple[str, Optional[str]]], List[str], Optional[str]]:
    prefix_cid = line_clean[:tm.start()].strip()
    eff_cid = prefix_cid or current_cue_id
    start_ts = format_timestamp(tm.group(1))
    end_ts = format_timestamp(tm.group(2)) if tm.group(2) else None
    trailing_text = line_clean[tm.end():].strip()
    if trailing_text:
        raw_cues.append({"cid": eff_cid, "start": start_ts, "end": end_ts, "body_lines": [trailing_text]})
        return None, [], None
    return (start_ts, end_ts), [], eff_cid


def _collect_raw_vtt_cues(raw_text: str) -> List[Dict[str, Any]]:
    lines = raw_text.lstrip("\ufeff").splitlines()
    raw_cues: List[Dict[str, Any]] = []
    current_cue_id: Optional[str] = None
    curr_tm: Optional[Tuple[str, Optional[str]]] = None
    curr_body: List[str] = []

    for i, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean or line_clean.startswith(("WEBVTT", "NOTE")):
            continue
        if _is_vtt_cue_id_line(lines, i, line_clean):
            current_cue_id = line_clean
            continue
        tm = TIME_RE.search(line_clean)
        if tm:
            if curr_tm:
                raw_cues.append({"cid": current_cue_id, "start": curr_tm[0], "end": curr_tm[1], "body_lines": curr_body})
            curr_tm, curr_body, current_cue_id = _consume_vtt_time_line(line_clean, tm, current_cue_id, raw_cues)
            continue
        if curr_tm is not None:
            curr_body.append(line_clean)

    if curr_tm:
        raw_cues.append({"cid": current_cue_id, "start": curr_tm[0], "end": curr_tm[1], "body_lines": curr_body})
    return raw_cues


def _build_structured_vtt_cue(raw_cue: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    spk = None
    text_parts: List[str] = []
    for line in raw_cue["body_lines"]:
        line_spk, line_txt = _parse_vtt_line_speaker_and_text(line)
        if line_spk is not None:
            spk = line_spk
        clean_txt = line_txt if line_txt is not None else re.sub(r"<[^>]+>", "", line).strip()
        if clean_txt and not clean_txt.isdigit():
            text_parts.append(clean_txt)
    full_text = " ".join(text_parts).strip()
    if not full_text:
        return None
    start_sec = _to_sec(raw_cue["start"])
    return {
        "cid": raw_cue["cid"], "start": raw_cue["start"], "end": raw_cue["end"],
        "st": start_sec, "en": _to_sec(raw_cue["end"]) if raw_cue["end"] else start_sec,
        "spk": spk, "text": full_text,
    }


def _extract_structured_vtt_cues(raw_cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    for raw_cue in raw_cues:
        built = _build_structured_vtt_cue(raw_cue)
        if built is not None:
            cues.append(built)
    return cues


def _fill_bridged_vtt_speakers(cues: List[Dict[str, Any]]) -> None:
    for i in range(1, len(cues) - 1):
        p, cur, n = cues[i - 1], cues[i], cues[i + 1]
        if cur["spk"] is None and p["spk"] and p["spk"] == n["spk"]:
            if (cur["st"] - p["en"] <= 3.0) and (n["st"] - cur["en"] <= 3.0):
                cur["spk"] = p["spk"]


def _propagate_vtt_speaker_continuity(cues: List[Dict[str, Any]]) -> None:
    _fill_bridged_vtt_speakers(cues)
    for i in range(1, len(cues)):
        if cues[i]["spk"] is None and cues[i - 1]["spk"] and (cues[i]["st"] - cues[i - 1]["en"] <= 2.5):
            cues[i]["spk"] = cues[i - 1]["spk"]
    for i in range(len(cues) - 2, -1, -1):
        if cues[i]["spk"] is None and cues[i + 1]["spk"] and (cues[i + 1]["st"] - cues[i]["en"] <= 1.5):
            cues[i]["spk"] = cues[i + 1]["spk"]


def parse_vtt_or_srt(raw_text: str) -> NormalizedTranscript:
    """Parse WebVTT or SRT subtitle content into canonical turns with speaker continuity."""
    raw_cues = _collect_raw_vtt_cues(raw_text)
    if not raw_cues:
        raise ValueError(
            "Stage U0 Ingestion Error: Failed to parse any dialogue turns from WebVTT/SRT source. "
            "Ensure the transcript is perfectly formatted or provide a clean markdown file."
        )
    cues = _extract_structured_vtt_cues(raw_cues)
    _propagate_vtt_speaker_continuity(cues)
    spans = [
        SourceSpan(start_time=c["start"], end_time=c["end"], speaker_label=c["spk"] if c["spk"] != "Unknown" else None, text=c["text"], cue_id=c["cid"])
        for c in cues
    ]
    turns = merge_spans_into_turns(spans)
    text = "\n".join(f"### [{t.start_time or '00:00:00'}]\n\n**{t.speaker or 'Unknown'}:** {t.text}\n" for t in turns)
    return NormalizedTranscript(text=text, turns=turns)


def _extract_copilot_items(data: Any) -> List[Dict[str, Any]]:
    """Extract list of turn/message objects from varied JSON structures."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("turns", "transcript", "messages", "utterances", "segments", "results"):
            val = data.get(k)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _extract_copilot_speaker(item: Dict[str, Any], idx: int) -> Optional[str]:
    """Extract speaker name from item dictionary."""
    for key in ("speaker", "speaker_name", "speakerName", "from", "user", "name"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return _clean_speaker_label_str(val.strip())
        if isinstance(val, dict):
            name = val.get("name") or val.get("displayName")
            if name and str(name).strip():
                return _clean_speaker_label_str(str(name).strip())
    return None


def _extract_copilot_timestamp(item: Dict[str, Any], idx: int) -> Optional[str]:
    """Extract timestamp string in HH:MM:SS format."""
    for key in ("timestamp", "time", "start", "start_time", "startTime", "offset"):
        val = item.get(key)
        if isinstance(val, (int, float)):
            total_sec = int(val)
            h = total_sec // 3600
            m = (total_sec % 3600) // 60
            s = total_sec % 60
            return f"{h:02d}:{m:02d}:{s:02d}"
        if val is not None and str(val).strip():
            return format_timestamp(str(val))
    return None


def _extract_copilot_text(item: Dict[str, Any]) -> str:
    """Extract utterance text from item dictionary."""
    for key in ("text", "content", "message", "body", "utterance"):
        val = item.get(key)
        if isinstance(val, dict):
            inner = val.get("text") or val.get("content") or ""
            if str(inner).strip():
                return str(inner).strip()
        elif val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _load_copilot_json_data(raw_text: str) -> Any:
    try:
        return json.loads(raw_text)
    except Exception:
        from dual_pipeline.validation.pydantic_gate import repair_json_tokens
        try:
            return json.loads(repair_json_tokens(raw_text))
        except Exception as e:
            raise ValueError(
                "Stage U0 Ingestion Error: Failed to parse transcript JSON payload. "
                "Ensure the file contains a valid JSON array or object with dialogue turns."
            ) from e


def _build_copilot_source_spans(items: List[Dict[str, Any]]) -> List[SourceSpan]:
    spans: List[SourceSpan] = []
    for idx, item in enumerate(items):
        speaker = _extract_copilot_speaker(item, idx)
        ts_str = _extract_copilot_timestamp(item, idx)
        text = _extract_copilot_text(item)
        if text:
            spans.append(SourceSpan(
                start_time=ts_str if ts_str and "idx" not in ts_str else None,
                speaker_label=speaker if speaker and not speaker.startswith("Speaker_") else None,
                text=text, record_id=item.get("id") or item.get("messageId"),
            ))
    return spans


def parse_copilot_json(raw_text: str) -> NormalizedTranscript:
    """Parse CoPilot / Otter / Teams Graph JSON transcript exports."""
    data = _load_copilot_json_data(raw_text)
    items = _extract_copilot_items(data)
    if not items:
        raise ValueError(
            "Stage U0 Ingestion Error: Parsed JSON payload successfully but failed to extract any dialogue turns. "
            "Verify the JSON structure matches CoPilot / Teams / Otter exports."
        )

    spans = _build_copilot_source_spans(items)
    if not spans:
        raise ValueError(
            "Stage U0 Ingestion Error: Parsed JSON payload contains no text dialogue turns. "
            "Refusing to emit unparsed text."
        )

    turns = merge_spans_into_turns(spans)
    text_out = "\n".join(f"### [{t.start_time or '00:00:00'}]\n\n**{t.speaker or 'Unknown'}:** {t.text}\n" for t in turns)
    return NormalizedTranscript(text=text_out, turns=turns)


def parse_legacy_msg(raw_bytes: bytes) -> str:
    """Extract legacy MSG formats using extract_msg. Fails closed on invalid or corrupted formats."""
    try:
        import extract_msg
    except ImportError as e:
        raise RuntimeError(f"extract_msg is required to parse MSG format: {e}") from e

    try:
        msg = extract_msg.Message(raw_bytes)
        body = msg.body or ""
        if not body.strip():
            raise ValueError("Legacy MSG payload contains no extractable body text.")
        return body.strip()
    except Exception as e:
        raise ValueError(f"Failed to parse legacy MSG payload: {e}") from e


def parse_legacy_ppt(raw_bytes: bytes) -> str:
    """Extract legacy PPT (binary). Fails closed without external converter."""
    raise ValueError(
        "Legacy binary PPT (.ppt) extraction is not supported without external presentation converter. "
        "Convert to modern PPTX (.pptx) or export as PDF before ingestion."
    )

