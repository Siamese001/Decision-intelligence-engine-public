"""
dual_pipeline/manifest.py

Declarative schema contract (IngestionMeetingManifest) defining meeting metadata,
speaker normalization, ASR replacements, roster data, and pipeline artifact targets.
Conforms strictly to Layer 0 (Contracts & Primitives).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dual_pipeline.temporal_ssot import TemporalSSOT

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class IngestionMeetingManifest:
    """Declarative manifest capturing complete metadata for a meeting ingestion run."""

    project_name: str
    meeting_title: str
    meeting_date: Union[str, date, datetime]
    start_time_et: str
    end_time_et: str
    base_name: Optional[str] = None
    meeting_archetype: str = "TECHNICAL_DEMONSTRATION"
    source_transcript_path: Optional[Union[str, Path]] = None
    raw_text: Optional[str] = None
    speaker_map: Dict[str, str] = field(default_factory=dict)
    term_replacements: Dict[str, str] = field(default_factory=dict)
    attendees: List[Dict[str, Any]] = field(default_factory=list)
    roster: List[Dict[str, str]] = field(default_factory=list)
    meeting_objective: str = ""
    expected_domain_terms: List[str] = field(default_factory=list)
    ambiguity_rows: List[Dict[str, str]] = field(default_factory=list)
    entity_clarifications: List[Dict[str, Any]] = field(default_factory=list)
    f2_text: Optional[str] = None
    f3_data: Optional[Dict[str, Any]] = None
    f2_text_path: Optional[Union[str, Path]] = None
    f3_data_path: Optional[Union[str, Path]] = None
    participants_md: Optional[str] = None
    manifest_dir: Optional[Path] = None
    start_system_event: str = ""
    stop_system_event: str = ""
    raw_header_note: str = ""
    classification: str = "confidential"
    audiences: List[str] = field(default_factory=lambda: ["partner_advisory", "client"])
    output_root: Optional[Union[str, Path]] = None
    intake_dir: Optional[Union[str, Path]] = None
    directive_id: Optional[str] = None

    @property
    def human_date(self) -> str:
        """Tier 2 date representation: MM/DD/YYYY."""
        return TemporalSSOT.to_human_date(self.meeting_date)

    @property
    def path_date(self) -> str:
        """Tier 3 date representation: MM-DD-YYYY."""
        return TemporalSSOT.to_path_date(self.meeting_date)

    @property
    def iso_date(self) -> str:
        """Tier 1 date representation: YYYY-MM-DD."""
        return TemporalSSOT.to_iso8601(self.meeting_date)

    @property
    def time_str(self) -> str:
        """Human readable time span with timezone."""
        return f"{self.start_time_et} - {self.end_time_et}"

    @property
    def time_slug(self) -> str:
        """Filesystem safe time slug for artifact naming."""
        clean = re.sub(r"[^A-Za-z0-9]", "", f"{self.start_time_et}_{self.end_time_et}")
        return clean.upper()

    @property
    def resolved_base_name(self) -> str:
        """Canonical base filename parameterized by meeting name, date, and time."""
        if self.base_name:
            return self.base_name
        clean_title = re.sub(r"[^A-Za-z0-9_]+", "_", self.meeting_title.strip()).strip("_")
        clean_proj = re.sub(r"[^A-Za-z0-9_]+", "_", self.project_name.strip()).strip("_")
        return f"{clean_proj.upper()}_{clean_title}_{self.path_date}_{self.time_slug}"

    @property
    def resolved_output_dir(self) -> Path:
        """Governed output directory hierarchy: output/<project_name>/<MM-DD-YYYY>/."""
        root = Path(self.output_root) if self.output_root else (ROOT / "output")
        return root / self.project_name / self.path_date

    @property
    def resolved_intake_dir(self) -> Path:
        """Intake directory for downstream package generation."""
        return Path(self.intake_dir) if self.intake_dir else (ROOT / "intake")

    @property
    def f1_filename(self) -> str:
        return f"01_{self.resolved_base_name}_hitl_resolved_transcript.md"

    @property
    def f2_filename(self) -> str:
        return f"02_{self.resolved_base_name}_executive_recap.md"

    @property
    def f3_filename(self) -> str:
        return f"03_{self.resolved_base_name}_canonical_signal_projection.json"

    @property
    def f1_path(self) -> Path:
        return self.resolved_output_dir / self.f1_filename

    @property
    def f2_path(self) -> Path:
        return self.resolved_output_dir / self.f2_filename

    @property
    def f3_path(self) -> Path:
        return self.resolved_output_dir / self.f3_filename

    @property
    def pkg_path(self) -> Path:
        return self.resolved_intake_dir / f"{self.resolved_base_name}.intake.json"

    @property
    def qa_report_path(self) -> Path:
        return self.resolved_output_dir / "qa_gates_report.json"

    @property
    def judge_report_path(self) -> Path:
        return self.resolved_output_dir / "llm_judge_evaluation.json"

    @property
    def release_decision_path(self) -> Path:
        return self.resolved_output_dir / "final_release_decision.json"

    def resolve_f2_text(self) -> Optional[str]:
        """Resolve and return f2_text either from memory or from external f2_text_path."""
        if self.f2_text:
            return self.f2_text
        if self.f2_text_path:
            p = Path(self.f2_text_path)
            if not p.is_absolute():
                base = self.manifest_dir or ROOT
                p = (base / p).resolve()
            if p.is_file():
                self.f2_text = p.read_text(encoding="utf-8")
                return self.f2_text
        return None

    def resolve_f3_data(self) -> Optional[Dict[str, Any]]:
        """Resolve and return f3_data either from memory or from external f3_data_path."""
        if self.f3_data:
            return self.f3_data
        if self.f3_data_path:
            import json
            p = Path(self.f3_data_path)
            if not p.is_absolute():
                base = self.manifest_dir or ROOT
                p = (base / p).resolve()
            if p.is_file():
                self.f3_data = json.loads(p.read_text(encoding="utf-8"))
                return self.f3_data
        return None

    def resolve_source_text(self) -> str:
        """Resolve raw source transcript text."""
        if self.raw_text:
            return self.raw_text
        if self.source_transcript_path:
            p = Path(self.source_transcript_path)
            if not p.is_absolute():
                base = self.manifest_dir or ROOT
                p = (base / p).resolve()
            if p.is_file():
                self.raw_text = p.read_text(encoding="utf-8")
                return self.raw_text
        raise ValueError("Could not resolve source transcript text from manifest")

    def validate(self) -> None:
        """Validate manifest fields against schema and governance invariants."""
        if not self.project_name or not self.project_name.strip():
            raise ValueError("project_name cannot be empty")
        if not self.meeting_title or not self.meeting_title.strip():
            raise ValueError("meeting_title cannot be empty")
        if not self.start_time_et or not self.end_time_et:
            raise ValueError("start_time_et and end_time_et are required")
        TemporalSSOT.parse_to_date(self.meeting_date)
        if not self.source_transcript_path and not self.raw_text:
            raise ValueError("Either source_transcript_path or raw_text must be provided")

    def to_dict(self) -> Dict[str, Any]:
        """Convert manifest to serializable dictionary."""
        d = {
            "project_name": self.project_name,
            "meeting_title": self.meeting_title,
            "meeting_date": self.human_date,
            "start_time_et": self.start_time_et,
            "end_time_et": self.end_time_et,
            "base_name": self.base_name,
            "meeting_archetype": self.meeting_archetype,
            "source_transcript_path": str(self.source_transcript_path) if self.source_transcript_path else None,
            "speaker_map": self.speaker_map,
            "term_replacements": self.term_replacements,
            "attendees": self.attendees,
            "roster": self.roster,
            "meeting_objective": self.meeting_objective,
            "expected_domain_terms": self.expected_domain_terms,
            "ambiguity_rows": self.ambiguity_rows,
            "entity_clarifications": self.entity_clarifications,
            "f2_text_path": str(self.f2_text_path) if self.f2_text_path else None,
            "f3_data_path": str(self.f3_data_path) if self.f3_data_path else None,
            "participants_md": self.participants_md,
            "start_system_event": self.start_system_event,
            "stop_system_event": self.stop_system_event,
            "raw_header_note": self.raw_header_note,
            "classification": self.classification,
            "audiences": self.audiences,
            "output_root": str(self.output_root) if self.output_root else None,
            "intake_dir": str(self.intake_dir) if self.intake_dir else None,
            "directive_id": self.directive_id,
        }
        return {k: v for k, v in d.items() if v is not None}

    def to_metadata(self) -> Dict[str, Any]:
        """Convert manifest to canonical pipeline metadata format."""
        meta = self.to_dict()
        meta.update({
            "project": self.project_name,
            "meeting_name": self.meeting_title,
            "date": self.human_date,
            "time": self.time_str,
            "classification": self.classification,
            "audiences": self.audiences,
            "attendees": self.attendees,
            "roster": self.roster,
            "meeting_objective": self.meeting_objective,
            "expected_domain_terms": self.expected_domain_terms,
            "entity_clarifications": self.entity_clarifications,
            "speaker_map": self.speaker_map,
            "term_replacements": self.term_replacements,
            "directive_id": self.directive_id,
            "meeting_archetype": self.meeting_archetype,
        })
        f2 = self.resolve_f2_text()
        if f2:
            meta["f2_text"] = f2
        f3 = self.resolve_f3_data()
        if f3:
            meta["f3_data"] = f3
        return meta

    def to_json(self, path: Union[str, Path]) -> None:
        """Write manifest to JSON file."""
        import json
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any], manifest_dir: Optional[Path] = None) -> IngestionMeetingManifest:
        """Construct IngestionMeetingManifest from dictionary."""
        d = dict(data)
        d["manifest_dir"] = manifest_dir
        # Convert path strings if provided
        for k in ("source_transcript_path", "f2_text_path", "f3_data_path", "output_root", "intake_dir"):
            if d.get(k):
                d[k] = Path(d[k])
        manifest = cls(**d)
        manifest.validate()
        return manifest

    @classmethod
    def from_json(cls, path: Union[str, Path]) -> IngestionMeetingManifest:
        """Load and validate IngestionMeetingManifest from JSON file."""
        import json
        p = Path(path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Manifest file not found: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls.from_dict(data, manifest_dir=p.parent)
