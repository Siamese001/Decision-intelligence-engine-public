"""
Type definitions and data models for multimodal meeting attachment ingestion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class AttachmentType(str, Enum):
    """Governed classification of meeting collateral and attachments."""
    IMAGE = "image"
    DIAGRAM = "diagram"
    PRESENTATION = "presentation"
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    UNKNOWN = "unknown"


@dataclass
class AttachmentRecord:
    """Metadata and filesystem pointers for an ingested attachment."""
    attachment_id: str
    original_path: Path
    staged_path: Path  # Path inside referenced_artifacts/
    attachment_type: AttachmentType
    filename: str
    file_size_bytes: int
    sha256: str
    relative_staged_path: str = ""  # e.g. "referenced_artifacts/diagram.png"
    mime_type: Optional[str] = None
    description: Optional[str] = None
    # Collateral provenance: set automatically when file was dropped during live copilot session
    attribution_mode: str = "BACKGROUND"  # "BACKGROUND" | "COLLATERAL_REVIEWED"
    dropped_at_elapsed: Optional[str] = None      # "HH:MM:SS" — elapsed time at moment of drop

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "original_path": str(self.original_path),
            "staged_path": str(self.staged_path),
            "attachment_type": self.attachment_type.value,
            "filename": self.filename,
            "file_size_bytes": self.file_size_bytes,
            "sha256": self.sha256,
            "relative_staged_path": self.relative_staged_path,
            "mime_type": self.mime_type,
            "description": self.description,
            "attribution_mode": self.attribution_mode,
            "dropped_at_elapsed": self.dropped_at_elapsed,
        }


@dataclass
class ExtractedAttachmentContent:
    """Structured extraction payload from visual, slide, or document attachments."""
    attachment_id: str
    filename: str
    attachment_type: AttachmentType
    sha256: str
    title: Optional[str] = None
    extracted_text: str = ""
    summary: str = ""
    components: List[Dict[str, Any]] = field(default_factory=list)
    connections: List[Dict[str, Any]] = field(default_factory=list)
    key_topics: List[str] = field(default_factory=list)
    tables: List[Dict[str, Any]] = field(default_factory=list)
    claims: List[Dict[str, Any]] = field(default_factory=list)
    milestones: List[Dict[str, Any]] = field(default_factory=list)
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    has_diagrams: bool = False
    extraction_status: str = "SUCCESS"  # "SUCCESS" | "FAILED" | "EMPTY" | "PARTIAL"
    error_reason: Optional[str] = None
    error_type: Optional[str] = None
    is_failed: bool = False
    is_demo_or_sample: bool = False
    page_number: Optional[int] = None
    region_locator: Optional[Dict[str, Any]] = None
    extracted_at: str = ""

    @property
    def detected_components(self) -> List[Dict[str, Any]]:
        return self.components

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": "urn:nee:dual-pipeline:visual-content:1.0.0",
            "schema_version": "1.0.0",
            "attachment_id": self.attachment_id,
            "filename": self.filename,
            "attachment_type": self.attachment_type.value,
            "sha256": self.sha256,
            "title": self.title,
            "extracted_text": self.extracted_text,
            "summary": self.summary,
            "components": self.components,
            "connections": self.connections,
            "key_topics": self.key_topics,
            "tables": self.tables,
            "claims": self.claims,
            "milestones": self.milestones,
            "has_diagrams": self.has_diagrams,
            "extraction_status": self.extraction_status,
            "error_reason": self.error_reason,
            "error_type": self.error_type,
            "is_failed": self.is_failed,
            "is_demo_or_sample": self.is_demo_or_sample,
            "page_number": self.page_number,
            "region_locator": self.region_locator,
            "extracted_at": self.extracted_at,
        }


@dataclass
class AttachmentMention:
    """Spoken reference to an uploaded attachment in transcript dialogue."""
    turn_index: int
    speaker: str
    timestamp: str
    turn_text: str
    attachment_id: str
    filename: str
    matched_keyword: str
    is_weak_alignment: bool = False
    is_speaker_authenticated: bool = False


@dataclass
class CrossModalAttachmentAlignment:
    """Overall alignment between transcript turns and uploaded meeting attachments."""
    total_attachments: int
    mentions: List[AttachmentMention] = field(default_factory=list)
    turn_alignments: Dict[int, List[str]] = field(default_factory=dict)  # turn_idx -> list of attachment_ids
    unreferenced_attachments: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_attachments": self.total_attachments,
            "mentions": [
                {
                    "turn_index": m.turn_index,
                    "speaker": m.speaker,
                    "timestamp": m.timestamp,
                    "turn_text": m.turn_text,
                    "attachment_id": m.attachment_id,
                    "filename": m.filename,
                    "matched_keyword": m.matched_keyword,
                }
                for m in self.mentions
            ],
            "turn_alignments": self.turn_alignments,
            "unreferenced_attachments": self.unreferenced_attachments,
        }


@dataclass
class CollateralDrop:
    """Timestamped record of a file dropped into referenced_artifacts/ during a live copilot session."""
    filename: str
    dropped_at_elapsed: str       # "HH:MM:SS" — session elapsed time at moment of filesystem event
    sha256: str
    attribution_mode: str = "COLLATERAL_REVIEWED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "dropped_at_elapsed": self.dropped_at_elapsed,
            "sha256": self.sha256,
            "attribution_mode": self.attribution_mode,
        }


@dataclass
class CollateralAnnotation:
    """Embed hint produced by the aligner for each COLLATERAL_REVIEWED attachment."""
    attachment_id: str
    filename: str
    dropped_at_elapsed: str              # when the file was dropped
    aligned_turn_range: Tuple[str, str]  # (earliest_turn_ts, latest_turn_ts) in transcript
    staged_image_path: Optional[str]     # relative path to rendered PNG thumbnail, if available
    key_topics: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "filename": self.filename,
            "dropped_at_elapsed": self.dropped_at_elapsed,
            "aligned_turn_range": list(self.aligned_turn_range),
            "staged_image_path": self.staged_image_path,
            "key_topics": self.key_topics,
        }
