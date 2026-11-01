"""
Helper functions for date normalization, stem extraction, and project affinity checking.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .constants import PROJECT_AFFINITY_MAP


def _extract_date_token(val: str) -> Optional[str]:
    """Extracts normalized MM-DD-YYYY from date folder or filename stem."""
    m = re.search(r"\b(0?[1-9]|1[0-2])[-.](0?[1-9]|[12]\d|3[01])[-.](19|20)\d{2}\b", val)
    return f"{m.group(1).zfill(2)}-{m.group(2).zfill(2)}-{m.group(3)}" if m else None


def _validate_project_affinity(stem: str, proj_name: str, container_label: str) -> Optional[str]:
    """Detects cross-project deliverable misplacement based on explicit filename prefix or embedded workstream tokens."""
    stem_norm = re.sub(
        r"^(?:0?[1-9]|1[0-2])[-.](?:0?[1-9]|[12]\d|3[01])[-.](?:19|20)\d{2}[_\s]*",
        "",
        stem,
    ).lower()
    # 1. Leading prefix match
    for pfix, allowed in sorted(PROJECT_AFFINITY_MAP.items(), key=lambda x: -len(x[0])):
        if re.match(rf"^{re.escape(pfix)}(?:[\s_-]|$)", stem_norm):
            if proj_name not in allowed:
                return (
                    f"[PROJECT_AFFINITY_MISMATCH] Deliverable '{stem}' with prefix '{pfix}' "
                    f"resides in mismatched project '{proj_name}' ('{container_label}'). "
                    f"Expected one of: {sorted(allowed)}."
                )
            return None

    # 2. Embedded workstream token match (e.g. collateral_dociq_*, subject_dociq_*, hrit_sow_*)
    embedded_check_tokens = ("dociq", "jarvis", "conduit", "regai", "hrit", "enterprise4u", "hr360")
    tokens = set(re.split(r"[^a-z0-9]+", stem_norm))
    for tok in embedded_check_tokens:
        if tok in tokens and tok in PROJECT_AFFINITY_MAP:
            allowed = PROJECT_AFFINITY_MAP[tok]
            if proj_name not in allowed:
                return (
                    f"[PROJECT_AFFINITY_MISMATCH] Deliverable '{stem}' with embedded workstream '{tok}' "
                    f"resides in mismatched project '{proj_name}' ('{container_label}'). "
                    f"Expected one of: {sorted(allowed)}."
                )
            return None
    return None


def _extract_stem(fname: str) -> str:
    """Extracts meeting stem from deliverable filename."""
    body = fname[3:]
    suffixes = (
        "_hitl_resolved_transcript.md",
        "_Transcript.md",
        "_transcript.md",
        "_executive_recap.md",
        "_Recap.md",
        "_recap.md",
        "_canonical_signal_projection.json",
        "_Signals.json",
        "_signals.json",
        ".md",
        ".json",
    )
    for s in suffixes:
        if body.endswith(s):
            return body[:-len(s)]
    return Path(body).stem
