"""Forensic Evidence Envelope Resolver (Layer 2).

Indexes Stage U0/L1 transcripts and canonical signal projections (03_*.json)
to provide exact evidence spans, timestamps, and verbatim dialogue citations.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from dual_pipeline.graph_constrained_projection.models import EvidenceSpan

logger = logging.getLogger("dual_pipeline.graph_constrained_projection.evidence_envelope")


class EvidenceEnvelopeResolver:
    """Extracts and resolves evidence spans from canonical project deliverables."""

    def __init__(self, project_dir: Optional[str | Path] = None) -> None:
        self._project_dir = Path(project_dir or "output/project_lens")
        self._spans: Dict[str, EvidenceSpan] = {}
        self._index_transcripts_and_projections()

    @property
    def total_spans(self) -> int:
        return len(self._spans)

    def _index_transcripts_and_projections(self) -> None:
        """Walks project deliverables to index evidence spans."""
        if not self._project_dir.exists():
            logger.warning("Project directory %s not found", self._project_dir)
            return

        # 1. Index 03_*_canonical_signal_projection.json
        for proj_file in self._project_dir.glob("**/*canonical_signal_projection.json"):
            try:
                with open(proj_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                evidence_idx = data.get("evidence_index", {})
                for ev_id, item in evidence_idx.items():
                    speaker = item.get("speaker") or "UNKNOWN"
                    locator = item.get("locator", {})
                    ts = locator.get("value") if isinstance(locator, dict) else str(locator)
                    text = item.get("exact_text") or item.get("text") or ""
                    attr_mode = item.get("attribution_mode") or "EXPLICIT"

                    self._spans[ev_id] = EvidenceSpan(
                        evidence_id=ev_id,
                        source_file=proj_file.name,
                        speaker=speaker,
                        timestamp=ts,
                        verbatim_text=text,
                        attribution_mode=attr_mode,
                    )
            except Exception as exc:
                logger.debug("Failed parsing %s: %s", proj_file, exc)

        # 2. Index 01_*_hitl_resolved_transcript.md statements
        for transcript_file in self._project_dir.glob("**/*hitl_resolved_transcript.md"):
            try:
                self._index_markdown_transcript(transcript_file)
            except Exception as exc:
                logger.debug("Failed parsing %s: %s", transcript_file, exc)

    def _index_markdown_transcript(self, transcript_file: Path) -> None:
        """Parses timestamped speaker statements from forensic markdown."""
        with open(transcript_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Match blocks like: ### [00:15:14] -- stmt-018 \n\n **Amit Ayer:** Dialogue...
        stmt_pattern = re.compile(
            r"###\s*\[(\d{2}:\d{2}(?::\d{2})?)\](?:[^\n]*?(stmt-\d+))?\s*\n+^\*\*([^*]+)\*\*:\s*(.+?)(?=\n###|\Z)",
            re.MULTILINE | re.DOTALL,
        )

        for match in stmt_pattern.finditer(content):
            ts = match.group(1)
            stmt_tag = match.group(2)
            speaker = match.group(3).strip()
            dialogue = match.group(4).strip()

            key = stmt_tag if stmt_tag else f"{transcript_file.stem}:{ts}"
            if key not in self._spans:
                self._spans[key] = EvidenceSpan(
                    evidence_id=key,
                    source_file=transcript_file.name,
                    speaker=speaker,
                    timestamp=ts,
                    verbatim_text=dialogue[:300],  # bounded quote
                    attribution_mode="FORENSIC_TRANSCRIPT",
                )

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceSpan]:
        """Fetch indexed evidence span by ID."""
        return self._spans.get(evidence_id)

    def search_by_keyword(self, keyword: str, limit: int = 5) -> List[EvidenceSpan]:
        """Find evidence spans containing a given keyword."""
        kw_lower = keyword.lower()
        results = []
        for span in self._spans.values():
            if kw_lower in span.verbatim_text.lower():
                results.append(span)
                if len(results) >= limit:
                    break
        return results
