"""
dual_pipeline/document_pipeline_core/conversion_cache.py

Deterministic Conversion Artifact Caching and Provenance Binding.
Ensures conversion reuse requires exact matching raw content SHA-256 AND converter
version digests. Changed workbook content or converter logic immediately invalidates reuse.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Tuple, Union

from dual_pipeline.source_classification.capability_registry import get_converter_versions

logger = logging.getLogger(__name__)

CONVERTER_VERSIONS: dict[str, str] = get_converter_versions()


@dataclass(frozen=True)
class ConversionArtifactRecord:
    cache_key: str
    content_sha256: str
    converter_version: str
    format: str
    text_sha256: str
    char_count: int
    artifact_path: Optional[str] = None
    coverage_proof: Optional[dict[str, Any]] = None
    source_units: Optional[list[dict[str, Any]]] = None
    slide_units: Optional[list[dict[str, Any]]] = None
    visual_extraction: Optional[dict[str, Any]] = None


def _extract_cache_options_fingerprint(metadata: Optional[dict[str, Any]]) -> str:
    if not metadata:
        return ""
    opts: dict[str, Any] = {}
    for k in ("selected_sheets", "sheet_names", "ocr_mode", "enable_visual_ocr", "is_collateral", "allow_collateral_transcript"):
        if k in metadata and metadata[k] is not None:
            val = metadata[k]
            if isinstance(val, (list, tuple, set)):
                opts[k] = sorted(str(x).strip() for x in val if str(x).strip())
            elif isinstance(val, str) and k in ("selected_sheets", "sheet_names"):
                opts[k] = sorted(s.strip() for s in val.split(",") if s.strip())
            else:
                opts[k] = val
    if not opts:
        return ""
    return json.dumps(opts, sort_keys=True, separators=(",", ":"))


def compute_conversion_cache_key(
    raw_bytes: bytes,
    format_name: str,
    converter_version: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Tuple[str, str, str]:
    """
    Compute deterministic cache key from raw bytes, converter version, and extraction options.

    Returns:
        Tuple of (cache_key, content_sha256, converter_version)
    """
    content_sha = hashlib.sha256(raw_bytes).hexdigest()
    eff_version = converter_version or CONVERTER_VERSIONS.get(
        format_name.lower(), f"{format_name}-default-v1.0.0"
    )
    opts_fp = _extract_cache_options_fingerprint(metadata)
    combined_str = f"{content_sha}:{eff_version}" if not opts_fp else f"{content_sha}:{eff_version}:{opts_fp}"
    cache_key = hashlib.sha256(combined_str.encode("utf-8")).hexdigest()
    return cache_key, content_sha, eff_version


def _is_cache_disabled(metadata: Optional[dict[str, Any]]) -> bool:
    """Check whether caching is opted-out in metadata."""
    if not metadata:
        return False
    return bool(
        metadata.get("no_cache")
        or metadata.get("disable_conversion_cache")
        or metadata.get("conversion_cache") is False
        or metadata.get("force_rerun")
    )


def _resolve_target_output_cache(target_out: Any, clean_proj: str) -> Optional[Path]:
    """Resolve conversion cache inside target output directory when configured."""
    if not target_out or str(target_out) == "auto":
        return None
    base_target = Path(target_out).resolve()
    if "output" in base_target.parts:
        return None
    if base_target.name == clean_proj or clean_proj in str(base_target):
        cache_p = base_target / "conversion_cache"
    else:
        cache_p = base_target / "conversion_cache" / clean_proj
    cache_p.mkdir(parents=True, exist_ok=True)
    return cache_p


def resolve_default_conversion_cache_dir(
    metadata: Optional[dict[str, Any]] = None,
    explicit_cache_dir: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """
    Resolve per-project conversion cache directory.
    Returns None if caching is explicitly opted-out via flag or metadata.
    """
    if _is_cache_disabled(metadata):
        return None

    if explicit_cache_dir:
        p = Path(explicit_cache_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    if metadata and metadata.get("conversion_cache_dir"):
        p = Path(metadata["conversion_cache_dir"]).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    project = (metadata.get("project") if metadata else None) or "default"
    import re
    clean_proj = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", str(project)).strip("_") or "default"

    target_out = (metadata.get("target_output_dir") or metadata.get("output_dir")) if metadata else None
    target_cache = _resolve_target_output_cache(target_out, clean_proj)
    if target_cache is not None:
        return target_cache

    from dual_pipeline.core import ROOT
    cache_p = ROOT / ".local" / "conversion_cache" / clean_proj
    cache_p.mkdir(parents=True, exist_ok=True)
    return cache_p


def get_cached_conversion(
    cache_key: str,
    cache_dir: Union[str, Path],
) -> Optional[dict[str, Any]]:
    """Retrieve cached conversion artifact and metadata if valid."""
    c_dir = Path(cache_dir)
    manifest_path = c_dir / f"{cache_key}.json"
    content_path = c_dir / f"{cache_key}.md"

    if not (manifest_path.is_file() and content_path.is_file()):
        return None

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        text = content_path.read_text(encoding="utf-8")
        actual_text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual_text_sha != data.get("text_sha256"):
            logger.warning("Corrupted conversion artifact for cache key %s", cache_key)
            return None
        data["text"] = text
        return data
    except Exception as exc:
        logger.warning("Failed reading cached conversion for %s: %s", cache_key, exc)
        return None


def save_conversion_artifact(
    cache_key: str,
    text: str,
    format_name: str,
    content_sha256: str,
    converter_version: str,
    cache_dir: Union[str, Path],
    coverage_proof: Optional[dict[str, Any]] = None,
    source_units: Optional[list[dict[str, Any]]] = None,
    slide_units: Optional[list[dict[str, Any]]] = None,
    visual_extraction: Optional[dict[str, Any]] = None,
) -> ConversionArtifactRecord:
    """Save immutable conversion artifact and its provenance metadata."""
    c_dir = Path(cache_dir)
    c_dir.mkdir(parents=True, exist_ok=True)

    content_path = c_dir / f"{cache_key}.md"
    manifest_path = c_dir / f"{cache_key}.json"

    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    content_path.write_text(text, encoding="utf-8")

    record = ConversionArtifactRecord(
        cache_key=cache_key,
        content_sha256=content_sha256,
        converter_version=converter_version,
        format=format_name,
        text_sha256=text_sha,
        char_count=len(text),
        artifact_path=str(content_path),
        coverage_proof=coverage_proof,
        source_units=source_units,
        slide_units=slide_units,
        visual_extraction=visual_extraction,
    )
    manifest_path.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
    return record


def _unpack_loader_result(
    raw_res: Union[str, Tuple[Any, ...]], default_format: str
) -> Tuple[str, Any, Any, str, Any, Any]:
    if not isinstance(raw_res, tuple):
        return raw_res, None, None, default_format, None, None
    padded = list(raw_res) + [None] * 6
    eff_format = str(padded[3]) if padded[3] else default_format
    return padded[0], padded[1], padded[2], eff_format, padded[4], padded[5]


def resolve_conversion_with_cache(
    source_p: Path,
    raw_bytes: bytes,
    format_name: str,
    loader_fn: Callable[[], Union[str, Tuple[Any, ...]]],
    cache_dir: Optional[Union[str, Path]] = None,
    converter_version: Optional[str] = None,
    force_recompute: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> Tuple[str, str, bool, Optional[ConversionArtifactRecord]]:
    """
    Resolve document text, reusing cached conversion only if content SHA-256 and
    converter version match exactly.

    Returns:
        Tuple of (text, cache_key, was_cached, record)
    """
    cache_key, content_sha, eff_version = compute_conversion_cache_key(
        raw_bytes, format_name, converter_version, metadata=metadata
    )

    if not force_recompute and cache_dir:
        cached = get_cached_conversion(cache_key, cache_dir)
        if cached is not None:
            logger.info("Conversion cache HIT for %s (key=%s)", source_p.name, cache_key[:12])
            record = ConversionArtifactRecord(
                cache_key=cache_key,
                content_sha256=cached["content_sha256"],
                converter_version=cached["converter_version"],
                format=cached["format"],
                text_sha256=cached["text_sha256"],
                char_count=cached["char_count"],
                artifact_path=cached.get("artifact_path"),
                coverage_proof=cached.get("coverage_proof"),
                source_units=cached.get("source_units"),
                slide_units=cached.get("slide_units"),
                visual_extraction=cached.get("visual_extraction"),
            )
            return cached["text"], cache_key, True, record

    logger.info("Conversion cache MISS for %s (key=%s). Converting...", source_p.name, cache_key[:12])
    text, proof, units, eff_format, slide_units, visual_ext = _unpack_loader_result(
        loader_fn(), format_name
    )

    record = None
    if cache_dir:
        record = save_conversion_artifact(
            cache_key=cache_key,
            text=text,
            format_name=eff_format,
            content_sha256=content_sha,
            converter_version=eff_version,
            cache_dir=cache_dir,
            coverage_proof=proof,
            source_units=units,
            slide_units=slide_units,
            visual_extraction=visual_ext,
        )
    return text, cache_key, False, record

