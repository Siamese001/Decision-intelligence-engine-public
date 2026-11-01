"""Google provider adapter for Stage L1 header metadata extraction and validation."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.transcript_stages_core.external_synthesizer import (
    call_gemini_generate_content,
    clean_json_markdown,
)

logger = logging.getLogger(__name__)

# Strict MM/DD/YYYY date format matching AGENTS.md invariant
DATE_REGEX = re.compile(r"^(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/(20\d{2})$")
PROJECT_CODE_REGEX = re.compile(r"^[a-z0-9_\-]{2,64}$", re.IGNORECASE)
TIME_REGEX = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?$")

L1_METADATA_PROMPT = (
    "Extract meeting metadata from the following transcript snippet.\n"
    "Return JSON object with:\n"
    "- meeting_name (string, concise)\n"
    "- date (string, strictly MM/DD/YYYY format, e.g. '09/25/2026')\n"
    "- time (string, e.g. '10:00 AM')\n"
    "- project_name (string, e.g. 'project_nexus')\n"
    "- attendee_roster (array of strings with attendee names and roles/orgs)"
)


def validate_l1_header_metadata(metadata: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate L1 extracted metadata against strict project and date format invariants."""
    errors: List[str] = []
    if not isinstance(metadata, dict):
        return False, ["Metadata must be a dictionary"]

    date_str = str(metadata.get("date", "")).strip()
    if date_str and not DATE_REGEX.match(date_str):
        errors.append(f"Invalid date format '{date_str}'; must match strictly MM/DD/YYYY")

    project_str = str(metadata.get("project_name", "")).strip()
    if project_str and not PROJECT_CODE_REGEX.match(project_str):
        errors.append(f"Invalid project_name format '{project_str}'; must match alphanumeric/slug pattern")

    time_str = str(metadata.get("time", "")).strip()
    if time_str and not TIME_REGEX.match(time_str):
        errors.append(f"Invalid time format '{time_str}'")

    roster = metadata.get("attendee_roster")
    if roster is not None and not isinstance(roster, (list, tuple)):
        errors.append("attendee_roster must be a list of strings")

    return len(errors) == 0, errors


def _sanitize_date(parsed: Dict[str, Any]) -> None:
    date_val = str(parsed.get("date", "")).strip()
    if not date_val or "-" not in date_val:
        return
    parts = date_val.split("-")
    if len(parts) == 3 and len(parts[0]) == 2 and len(parts[1]) == 2 and len(parts[2]) == 4:
        parsed["date"] = f"{parts[0]}/{parts[1]}/{parts[2]}"
    elif len(parts) == 3 and len(parts[0]) == 4:  # YYYY-MM-DD
        parsed["date"] = f"{parts[1]}/{parts[2]}/{parts[0]}"


def _tag_attendee_provenance(parsed: Dict[str, Any]) -> None:
    parsed.setdefault("provenance", "llm_inferred")
    parsed.setdefault("attendee_provenance", "llm_inferred")
    for key in ("attendees", "attendee_roster", "roster"):
        val = parsed.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    item.setdefault("provenance", "llm_inferred")


class L1GoogleMetadataAdapter:
    """Extracts and validates Tier-1 header metadata using Google Generative Language API."""

    def __init__(self, model: Optional[str] = None, deadline_seconds: float = 60.0):
        from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
        self.model = model or resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_FLASH)
        self.deadline_seconds = deadline_seconds

    def extract_header_metadata(
        self, transcript: str, hint_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Extract header metadata from transcript snippet (~1000 tokens) with schema enforcement."""
        snippet = transcript[:4000]
        context = f"\nContextual hints: {json.dumps(hint_metadata, sort_keys=True)}\n" if hint_metadata else ""
        user_content = f"{context}Transcript snippet:\n{snippet}"

        try:
            raw_response = call_gemini_generate_content(
                model=self.model,
                system_prompt=L1_METADATA_PROMPT,
                user_content=user_content,
                json_mode=True,
                deadline_seconds=self.deadline_seconds,
            )
            cleaned = clean_json_markdown(raw_response)
            parsed = json.loads(cleaned)
            if not isinstance(parsed, dict):
                logger.warning("L1 Google metadata adapter returned non-dict JSON: %s", parsed)
                return {}

            is_valid, validation_errors = validate_l1_header_metadata(parsed)
            if not is_valid:
                logger.warning("L1 Google metadata validation warnings: %s", validation_errors)
                _sanitize_date(parsed)

            _tag_attendee_provenance(parsed)
            return parsed
        except Exception as exc:
            logger.warning("L1 Google metadata extraction failed: %s", exc)
            return {}
