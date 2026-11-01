"""
dual_pipeline.attachment_core.collateral_manifest

Produces collateral_manifest.json — the governed provenance receipt that the
LLM judge uses to validate COLLATERAL_REVIEWED grounding claims in Output 3.

Format:
{
  "schema_version": "1.0",
  "items": [
    {
      "attachment_id": "att-01-lens_v1_2_slide3",
      "filename": "LENS_v1.2_slide3.png",
      "attribution_mode": "COLLATERAL_REVIEWED",
      "dropped_at_elapsed": "00:15:22",
      "aligned_turn_range": ["00:14:00", "00:17:00"],
      "staged_image_path": "referenced_artifacts/LENS_v1.2_slide3.png",
      "sha256": "abc123..."
    }
  ]
}
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import AttachmentRecord, CollateralAnnotation

logger = logging.getLogger(__name__)

COLLATERAL_MANIFEST_SCHEMA_VERSION = "1.0"


def build_collateral_manifest(
    records: List[AttachmentRecord],
    annotations: Optional[List[CollateralAnnotation]] = None,
) -> Dict[str, Any]:
    """
    Constructs the collateral manifest dict from staged AttachmentRecords and
    optional CollateralAnnotations (which carry aligned_turn_range and key_topics).

    Only COLLATERAL_REVIEWED records are included; BACKGROUND records are omitted.
    """
    annotations_by_id: Dict[str, CollateralAnnotation] = {}
    if annotations:
        annotations_by_id = {a.attachment_id: a for a in annotations}

    items = []
    for rec in records:
        if rec.attribution_mode != "COLLATERAL_REVIEWED":
            continue
        ann = annotations_by_id.get(rec.attachment_id)
        item: Dict[str, Any] = {
            "attachment_id": rec.attachment_id,
            "filename": rec.filename,
            "attribution_mode": rec.attribution_mode,
            "dropped_at_elapsed": rec.dropped_at_elapsed or "00:00:00",
            "staged_image_path": rec.relative_staged_path or None,
            "sha256": rec.sha256,
        }
        if ann:
            item["aligned_turn_range"] = list(ann.aligned_turn_range)
            item["key_topics"] = ann.key_topics
        items.append(item)

    return {
        "schema_version": COLLATERAL_MANIFEST_SCHEMA_VERSION,
        "items": items,
    }


def write_collateral_manifest(
    records: List[AttachmentRecord],
    output_dir: Path,
    annotations: Optional[List[CollateralAnnotation]] = None,
) -> Optional[Path]:
    """
    Writes collateral_manifest.json into output_dir.

    Returns the path written, or None if there were no COLLATERAL_REVIEWED items.
    """
    manifest = build_collateral_manifest(records, annotations)
    if not manifest["items"]:
        logger.debug("No COLLATERAL_REVIEWED attachments — skipping collateral_manifest.json")
        return None

    out_path = Path(output_dir) / "collateral_manifest.json"
    try:
        out_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "collateral_manifest.json written: %d items -> %s",
            len(manifest["items"]),
            out_path,
        )
        return out_path
    except OSError as exc:
        logger.warning("Failed to write collateral_manifest.json: %s", exc)
        return None


def load_collateral_manifest(output_dir: Path) -> Optional[Dict[str, Any]]:
    """
    Loads collateral_manifest.json from output_dir if present.
    Returns None if not found or parse fails.
    """
    manifest_path = Path(output_dir) / "collateral_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load collateral_manifest.json: %s", exc)
        return None


def is_collateral_grounded(
    attachment_id: str,
    manifest: Optional[Dict[str, Any]],
) -> bool:
    """
    Returns True if attachment_id appears in the manifest with
    attribution_mode='COLLATERAL_REVIEWED'. Used by Gate 2.9 and the judge
    rubric to validate grounding claims.
    """
    if not manifest:
        return False
    for item in manifest.get("items", []):
        if item.get("attachment_id") == attachment_id and \
                item.get("attribution_mode") == "COLLATERAL_REVIEWED":
            return True
    return False
