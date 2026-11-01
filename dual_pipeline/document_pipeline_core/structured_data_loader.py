"""
dual_pipeline/document_pipeline_core/structured_data_loader.py

Text-first Structured Data Loader for JSON Documents.
Extracts nested keys, arrays, and primitive leaf values with deterministic JSONPath
field-path locators (e.g. $.field.subfield, $[0].id) and coverage tracking.
Enforces strict XXE/script stripping, size limits, and nesting depth guards.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.document_pipeline_core.format_source_units import SourceUnit
from dual_pipeline.parameters import get_document_security_parameters

logger = logging.getLogger(__name__)

MAX_JSON_BYTES: int = 50 * 1024 * 1024  # 50 MiB
MAX_JSON_DEPTH: int = int(get_document_security_parameters()["max_json_depth"])
_SCRIPT_REGEX = re.compile(r"<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>", re.IGNORECASE)
_DANGEROUS_URI_REGEX = re.compile(r"(javascript|vbscript|data\s*:\s*text\/html)\s*:", re.IGNORECASE)


def _check_nesting_depth(obj: Any, current_depth: int = 1, max_depth: int = MAX_JSON_DEPTH) -> None:
    """Enforce maximum nesting depth to prevent recursion and stack-exhaustion attacks."""
    if current_depth > max_depth:
        raise ValueError(f"JSON nesting depth exceeds maximum allowed limit of {max_depth}.")
    if isinstance(obj, dict):
        for v in obj.values():
            _check_nesting_depth(v, current_depth + 1, max_depth)
    elif isinstance(obj, list):
        for item in obj:
            _check_nesting_depth(item, current_depth + 1, max_depth)


def _sanitize_string(val: str) -> str:
    """Strip embedded scripts, dangerous URI schemes, and control characters."""
    cleaned = _SCRIPT_REGEX.sub("", val)
    cleaned = _DANGEROUS_URI_REGEX.sub("[BLOCKED_URI]:", cleaned)
    return "".join(ch for ch in cleaned if ch in ("\n", "\r", "\t") or ord(ch) >= 32)


def _flatten_dict(
    obj: dict[str, Any],
    prefix: str,
    max_depth: int,
    current_depth: int,
) -> List[Tuple[str, Any, str]]:
    """Flatten dictionary keys into locator tuples."""
    if not obj:
        return [(prefix, "{}", "empty_object")]
    results: List[Tuple[str, Any, str]] = []
    for k, v in obj.items():
        clean_k = _sanitize_string(str(k))
        path = f"{prefix}.{clean_k}" if prefix != "$" else f"$.{clean_k}"
        if isinstance(v, (dict, list)):
            results.extend(_flatten_json(v, path, max_depth, current_depth + 1))
        else:
            results.append((path, v, type(v).__name__))
    return results


def _flatten_list(
    obj: list[Any],
    prefix: str,
    max_depth: int,
    current_depth: int,
) -> List[Tuple[str, Any, str]]:
    """Flatten list elements into indexed locator tuples."""
    if not obj:
        return [(prefix, "[]", "empty_array")]
    results: List[Tuple[str, Any, str]] = []
    for idx, item in enumerate(obj):
        path = f"{prefix}[{idx}]"
        if isinstance(item, (dict, list)):
            results.extend(_flatten_json(item, path, max_depth, current_depth + 1))
        else:
            results.append((path, item, type(item).__name__))
    return results


def _flatten_json(
    obj: Any,
    prefix: str = "$",
    max_depth: int = MAX_JSON_DEPTH,
    current_depth: int = 1,
) -> List[Tuple[str, Any, str]]:
    """Recursively flatten JSON structure into (locator, value, value_type) tuples."""
    if current_depth > max_depth:
        raise ValueError(f"JSON nesting depth exceeds maximum allowed limit of {max_depth}.")

    if isinstance(obj, dict):
        return _flatten_dict(obj, prefix, max_depth, current_depth)
    if isinstance(obj, list):
        return _flatten_list(obj, prefix, max_depth, current_depth)
    return [(prefix, obj, type(obj).__name__)]


def build_json_source_units(data: Any, source_hash: str) -> List[SourceUnit]:
    """Construct deterministic source units with JSONPath locators from parsed JSON."""
    _check_nesting_depth(data)
    leafs = _flatten_json(data)
    units: List[SourceUnit] = []

    for path, val, vtype in leafs:
        str_val = _sanitize_string(str(val)) if isinstance(val, str) else str(val)
        clean_content = f"{path}: {str_val}"
        units.append(
            SourceUnit(
                unit_id=f"{source_hash[:12]}:{path}",
                source_hash=source_hash,
                format="json",
                unit_type="field_path",
                locator=path,
                content=clean_content,
                metadata={
                    "path": path,
                    "value_type": vtype,
                    "raw_value": str_val,
                },
            )
        )
    return units


def _format_json_markdown(data: Any, leafs: List[Tuple[str, Any, str]], title: str) -> str:
    """Format structured JSON into human-readable Markdown with path locators."""
    lines: List[str] = [f"# Structured Data: {title}", "", "## Overview"]
    root_type = "Object" if isinstance(data, dict) else ("Array" if isinstance(data, list) else "Primitive")
    lines.append(f"- **Root Type**: {root_type}")
    lines.append(f"- **Field Count**: {len(leafs)}")
    lines.append("")

    lines.append("## Field Values & Locators")
    lines.append("| Field Path | Type | Value |")
    lines.append("| :--- | :--- | :--- |")

    for path, val, vtype in leafs[:250]:
        sval = _sanitize_string(str(val)).replace("|", "\\|").replace("\n", " ")
        if len(sval) > 80:
            sval = sval[:77] + "..."
        lines.append(f"| `{path}` | {vtype} | {sval} |")

    if len(leafs) > 250:
        lines.append(f"| ... | ... | *({len(leafs) - 250} additional fields omitted from summary table)* |")

    lines.append("")
    return "\n".join(lines)


def extract_json_with_locators(
    source_p: Path,
    raw_bytes: bytes,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Extract structured markdown and field-path source units from a JSON document."""
    if len(raw_bytes) > MAX_JSON_BYTES:
        raise ValueError(
            f"JSON payload exceeds maximum allowed size of {MAX_JSON_BYTES // (1024*1024)} MiB."
        )

    text = raw_bytes.decode("utf-8", errors="replace")
    if b"\x00" in raw_bytes[:4096]:
        raise ValueError("Binary NUL bytes detected in JSON payload.")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON syntax in '{source_p.name}': {exc}") from exc

    _check_nesting_depth(data)
    source_hash = hashlib.sha256(raw_bytes).hexdigest()
    units = build_json_source_units(data, source_hash)
    leafs = _flatten_json(data)
    title = source_p.stem.replace("_", " ").title()
    markdown = _format_json_markdown(data, leafs, title)

    if metadata is not None:
        coverage = {
            "total_records": len(units),
            "processed_records": len(units),
            "units_count": len(units),
            "fields_count": len(units),
            "complete": True,
            "root_type": "object" if isinstance(data, dict) else ("array" if isinstance(data, list) else "primitive"),
        }
        metadata["_source_coverage"] = coverage
        metadata["source_coverage"] = coverage
        metadata["source_units"] = [u.to_dict() for u in units]

    return markdown


def load_json_data(path: Path) -> Dict[str, Any]:
    """Governed callable adapter for JSON document attachments."""
    raw = path.read_bytes()
    meta: Dict[str, Any] = {}
    text = extract_json_with_locators(path, raw, meta)
    return {
        "text": text,
        "source_coverage": meta.get("source_coverage", {}),
        "source_units": meta.get("source_units", []),
    }
