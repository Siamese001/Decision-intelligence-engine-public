"""Visual document evidence envelope builder for screenshots and org charts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.attachment_core.image_extractor import extract_image_visual
from dual_pipeline.core import seal_artifact, stable_id


def build_visual_intake_envelope(
    image_path: Path,
    title: Optional[str] = None,
    project: str = "nea",
    classification: str = "internal",
    audiences: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build and cryptographically seal an evidence-envelope-intake package for a visual image."""
    image_path = Path(image_path).resolve()
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    raw_bytes = image_path.read_bytes()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    extraction = extract_image_visual(image_path)
    eff_title = title or extraction.get("title") or image_path.stem.replace("_", " ").title()
    text = extraction.get("text", "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    artifact_id = stable_id("artifact-visual-envelope", image_path.name, file_hash[:16])
    meeting_id = stable_id("meeting-visual", image_path.stem, file_hash[:16])

    # Build speaker representations from extracted text lines
    speakers: List[Dict[str, Any]] = []
    speaker_ref = stable_id("speaker-source", image_path.stem)
    statements = []
    for idx, line in enumerate(lines[:50]):
        statement_id = stable_id("stmt", artifact_id, str(idx))
        statements.append({
            "statement_id": statement_id,
            "kind": "technical",
            "text": line[:4000],
            "locator": {
                "kind": "region",
                "value": f"line-{idx + 1}",
            },
            "gemini_confidence": 0.95,
        })

    speakers.append({
        "speaker_ref": speaker_ref,
        "reported_name": "Visual OCR Engine",
        "email": None,
        "role_or_org": project.upper(),
        "statements": statements or [
            {
                "statement_id": stable_id("stmt-fallback", artifact_id),
                "kind": "technical",
                "text": eff_title,
                "locator": {"kind": "region", "value": "full-frame"},
                "gemini_confidence": 0.90,
            }
        ],
    })

    attendees = [s["reported_name"] for s in speakers if s.get("reported_name")]

    envelope: Dict[str, Any] = {
        "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
        "schema_version": "1.0.0",
        "artifact_id": artifact_id,
        "meeting": {
            "meeting_id": meeting_id,
            "title": eff_title[:256],
            "started_at": None,
            "organizer": "System Visual Ingress",
            "attendees": attendees or ["Visual OCR Engine"],
            "platform_reference": None,
            "google_meet_reference": None,
            "teams_reference": None,
            "source_location": str(image_path),
            "transcript_reference": None,
            "recording_reference": None,
            "source_hash": file_hash,
            "template_version": "1.0.0",
            "classification": classification,
            "audiences": audiences or ["team"],
        },
        "speakers": speakers,
        "actions": [],
        "adapter": {
            "name": "nee-visual-document-intake",
            "version": "1.0.0",
        },
        "extensions": {
            "source.profile": {
                "profile_version": "1.0.0",
                "source_id": artifact_id,
                "semantic_family": "visual_collateral",
                "media_modality": "visual",
                "container_type": "image_document",
                "classification_basis": "explicit_designation",
                "authority_rank": 3,
                "author": "Operator Visual Ingress",
                "approval_state": "approved",
                "observation_date": None,
                "effective_date": None,
                "origin_group_id": None,
                "confidentiality": classification,
            },
            "visual.metadata": {
                "ocr_line_count": len(lines),
                "raw_text_length": len(text),
                "visual_components_count": len(extraction.get("components", [])),
            },
        },
    }

    return seal_artifact(envelope)
