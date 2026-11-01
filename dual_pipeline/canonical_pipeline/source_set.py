"""
dual_pipeline/canonical_pipeline/source_set.py

Typed source-set and attachment manifest for multi-source ingestion.
Enforces per-source completion policy, distinct digests, basename deconfliction,
and documentary collateral isolation (preventing attachment text from contaminating
spoken utterances or speaker attributions).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class SourceMember:
    """Individual source document or attachment within a grouped ingestion set."""
    source_id: str
    path: str
    filename: str
    sha256: str
    byte_size: int
    format: str
    semantic_role: str = "primary_document"
    required: bool = True
    status: str = "PENDING"  # PENDING | PROCESSED | FAILED | EXCLUDED
    coverage: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "path": self.path,
            "filename": self.filename,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "format": self.format,
            "semantic_role": self.semantic_role,
            "required": self.required,
            "status": self.status,
            "coverage": self.coverage,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceMember:
        return cls(
            source_id=data["source_id"],
            path=data.get("path", ""),
            filename=data.get("filename", ""),
            sha256=data.get("sha256", ""),
            byte_size=data.get("byte_size", 0),
            format=data.get("format", ""),
            semantic_role=data.get("semantic_role", "primary_document"),
            required=data.get("required", True),
            status=data.get("status", "PENDING"),
            coverage=data.get("coverage", {}),
            error_message=data.get("error_message"),
        )


@dataclass
class SourceSetManifest:
    """Group manifest tracking multi-source document or meeting sets."""
    group_id: str
    members: List[SourceMember] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_group_complete: bool = False
    missing_required_members: List[str] = field(default_factory=list)

    def add_source_file(
        self,
        file_path: Union[str, Path],
        *,
        semantic_role: str = "primary_document",
        required: bool = True,
        explicit_source_id: Optional[str] = None,
    ) -> SourceMember:
        p = Path(file_path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Source file not found: {p}")
        
        raw_bytes = p.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        
        # Deconflict identical basenames using digest prefix
        src_id = explicit_source_id or f"{p.stem}_{digest[:8]}"
        ext = p.suffix.lower().lstrip(".")

        member = SourceMember(
            source_id=src_id,
            path=str(p),
            filename=p.name,
            sha256=digest,
            byte_size=len(raw_bytes),
            format=ext,
            semantic_role=semantic_role,
            required=required,
            status="PENDING",
        )
        self.members.append(member)
        self.verify_group_completeness()
        return member

    def record_member_outcome(
        self,
        source_id: str,
        *,
        status: str,
        coverage: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        for m in self.members:
            if m.source_id == source_id:
                m.status = status
                if coverage:
                    m.coverage = coverage
                if error_message:
                    m.error_message = error_message
                break
        self.verify_group_completeness()

    def verify_group_completeness(self) -> bool:
        missing = []
        for m in self.members:
            if m.required and m.status != "PROCESSED":
                missing.append(m.source_id)
        self.missing_required_members = missing
        self.is_group_complete = len(missing) == 0 and len(self.members) > 0
        return self.is_group_complete

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_id": self.group_id,
            "created_at": self.created_at,
            "total_members": len(self.members),
            "is_group_complete": self.is_group_complete,
            "missing_required_members": self.missing_required_members,
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceSetManifest:
        manifest = cls(
            group_id=data["group_id"],
            created_at=data.get("created_at", ""),
            is_group_complete=data.get("is_group_complete", False),
            missing_required_members=data.get("missing_required_members", []),
            members=[SourceMember.from_dict(m) for m in data.get("members", [])],
        )
        return manifest


def classify_transcript_attachment(
    attachment_path: Union[str, Path],
    *,
    required: bool = False,
) -> SourceMember:
    """
    Classify transcript collateral to enforce the strict boundary that attachments
    are documentary evidence only and never spoken turns or speaker identities.
    """
    p = Path(attachment_path).resolve()
    raw = p.read_bytes() if p.is_file() else b""
    digest = hashlib.sha256(raw).hexdigest()
    return SourceMember(
        source_id=f"att_{p.stem}_{digest[:8]}",
        path=str(p),
        filename=p.name,
        sha256=digest,
        byte_size=len(raw),
        format=p.suffix.lower().lstrip("."),
        semantic_role="document_attachment",
        required=required,
        status="PROCESSED" if raw else "FAILED",
    )
