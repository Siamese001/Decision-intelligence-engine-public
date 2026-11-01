"""Governed ID-Tag extraction and normalization engine for Project LENS.

Resolves the ID-tag drop defect (GEID/TEID stickers and nameplates)
by providing deterministic OCR normalization, regex healing, and zero-drop tracking.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class IDTagRecord:
    """Represents a raw ID-tag record detected by OCR or camera intake."""
    record_id: str
    asset_id: str
    raw_text: str
    confidence: float = 1.0
    bounding_box: Optional[Tuple[float, float, float, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IDTagNormalizationResult:
    """Outcome of ID-tag normalization and validation."""
    record_id: str
    asset_id: str
    raw_text: str
    is_valid: bool
    tag_type: Optional[str]  # "GEID" | "TEID"
    canonical_tag: Optional[str]
    confidence: float
    error_code: Optional[str] = None
    healed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IDTagExtractor:
    """Deterministic extractor and normalizer for turbine and solar asset ID tags."""

    # Heuristic prefix substitutions for common OCR distortions
    PREFIX_MAP = {
        "GEID": "GEID",
        "GE1D": "GEID",
        "GELD": "GEID",
        "GE-ID": "GEID",
        "TEID": "TEID",
        "TE1D": "TEID",
        "TELD": "TEID",
        "TE-ID": "TEID",
    }

    PREFIX_REGEX = re.compile(r"^(GE[1IIL]D|TE[1IIL]D|GE-ID|TE-ID)(?=[\s:\-#/_\\]|\b|$)", re.IGNORECASE)
    DELIMITER_REGEX = re.compile(r"[\s:\-#/_\\]+")

    @classmethod
    def normalize_tag(cls, raw: str) -> Tuple[Optional[str], Optional[str], bool, Optional[str]]:
        """Normalizes raw OCR tag text into canonical (tag_type, canonical_tag, healed, error)."""
        if not raw or not raw.strip():
            return None, None, False, "ERR_EMPTY_PAYLOAD"

        cleaned = raw.strip()
        # Collapse multi-line breaks and tabs
        cleaned = re.sub(r"[\r\n\t]+", " ", cleaned).strip()

        # Match known prefix
        prefix_match = cls.PREFIX_REGEX.match(cleaned)
        if not prefix_match:
            # Check if tag is purely numeric or missing prefix but follows asset convention
            numeric_match = re.match(r"^(\d{4,8})$", cleaned)
            if numeric_match:
                return "GEID", f"GEID-{numeric_match.group(1)}", True, None
            return None, None, False, "ERR_UNRECOGNIZED_PREFIX"

        matched_raw_prefix = prefix_match.group(1).upper().replace("-", "")
        # Map OCR substitution (e.g. GE1D -> GEID)
        canonical_prefix = "GEID" if "GE" in matched_raw_prefix else "TEID"
        remainder = cleaned[prefix_match.end():].strip()

        # Remove leading delimiters from remainder
        remainder = cls.DELIMITER_REGEX.sub("-", remainder).strip("-")
        if not remainder:
            return None, None, False, "ERR_MISSING_BODY"

        # Sanitize body: allow alphanumeric and hyphens only
        tokens = [re.sub(r"[^A-Z0-9]", "", tok.upper()) for tok in remainder.split("-")]
        tokens = [tok for tok in tokens if tok]

        if not tokens:
            return None, None, False, "ERR_INVALID_BODY"

        canonical_body = "-".join(tokens)
        canonical_tag = f"{canonical_prefix}-{canonical_body}"
        healed = (canonical_tag != cleaned)

        return canonical_prefix, canonical_tag, healed, None

    @classmethod
    def process_record(cls, record: IDTagRecord) -> IDTagNormalizationResult:
        """Processes a single raw ID tag record with zero silent drops."""
        tag_type, canonical_tag, healed, err = cls.normalize_tag(record.raw_text)
        is_valid = (err is None and canonical_tag is not None)

        conf = record.confidence
        if healed and conf > 0.0:
            # Minor penalty for OCR healing
            conf = round(max(0.70, conf * 0.95), 4)

        return IDTagNormalizationResult(
            record_id=record.record_id,
            asset_id=record.asset_id,
            raw_text=record.raw_text,
            is_valid=is_valid,
            tag_type=tag_type,
            canonical_tag=canonical_tag,
            confidence=conf if is_valid else 0.0,
            error_code=err,
            healed=healed,
        )

    @classmethod
    def process_batch(cls, records: List[IDTagRecord]) -> List[IDTagNormalizationResult]:
        """Processes a batch of records guaranteeing every record receives a result."""
        results = []
        for rec in records:
            results.append(cls.process_record(rec))
        return results
