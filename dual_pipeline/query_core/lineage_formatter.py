"""Deterministic query response formatter and lineage decoupling engine.

Separates readable narrative prose from raw knowledge graph provenance tokens.
Strips inline database hashes, assertion IDs, raw node identifiers, and footnote
markers (`[1]`, `[2]`), producing external-ready executive prose paired with a
consolidated Evidence & Graph Lineage traversal receipt table at the bottom.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .lineage_db import (
    CitationItem,
    format_confidence_badge as _format_confidence_badge,
    humanize_predicate as _humanize_predicate,
    humanize_token as _humanize_token,
    query_db_lineage_metadata as _query_db_lineage_metadata,
    resolve_graph_metadata,
    summarize_graph_traversal_coverage,
)

# Regex patterns identifying raw database provenance tokens
RE_ASSERTION_ID = re.compile(
    r"\b(?:ast_ing_[a-zA-Z0-9_]+|asrt_[a-zA-Z0-9_]+|assertion-[a-zA-Z0-9_\-]+|claim_[a-zA-Z0-9_]+|ev_legacy_edge_[a-zA-Z0-9_]+|edge_[a-zA-Z0-9_]+|retrieval-record-[a-zA-Z0-9_]+)\b"
)
RE_NODE_ID = re.compile(
    r"\b(?:workstream_|sow_|model_|pattern_|stakeholder_|person_|lane_|risk_|deliverable_|org_|opportunity_|staffing_)[a-z0-9_]+\b"
)
RE_FILE_URI = re.compile(r"file://[^\s\)\`]+")

LINEAGE_SECTION_HEADER = "### Evidence & Graph Lineage"
RE_LINEAGE_HEADER = re.compile(r"^###\s+Evidence\b[^\n]*", re.MULTILINE)

_ACRONYM_EXPANSIONS = ((re.compile(r"\bCLAHE\b"), "adaptive contrast enhancement"), (re.compile(r"\bVLM\b"), "Vision Language Model"))


@dataclasses.dataclass(frozen=True)
class SeamlessQueryResponse:
    clean_prose: str
    lineage_footer: str
    formatted_markdown: str
    citation_index_map: Dict[str, int]
    citations: List[CitationItem]


def _resolve_confidence_map(tokens: List[str], graph_path: Optional[str] = None) -> Dict[str, str]:
    """Resolves confidence badges for tokens from SQLite, defaulting missing to UNVERIFIED (0%)."""
    if not tokens:
        return {}
    _, confs, _, _ = resolve_graph_metadata(tokens, graph_path)
    return {t: confs.get(t, "UNVERIFIED (0%)") for t in tokens}


def _simplify_note_readability(text: str) -> str:
    """Expands unexplained technical acronyms and converts dense parentheticals into readable note clauses."""
    result = text
    for pattern, expansion in _ACRONYM_EXPANSIONS:
        result = pattern.sub(expansion, result)
    # Convert parenthetical examples (e.g., X) or (i.e., X) into readable em-dash note clauses
    result = re.sub(
        r"\s*\(\s*(?:e\.g\.|i\.e\.)\s*,?\s*([^()\n]+)\)",
        r" — such as \1",
        result,
    )
    # Convert multi-item parenthetical lists (item1, item2, ...) not preceded by ']' into em-dash clauses
    result = re.sub(
        r"(?<!\])\s*\(\s*([^()\n]+,[^()\n]+)\)",
        r" — \1",
        result,
    )
    return result


def _clean_narrative_block(text: str) -> Tuple[str, List[str]]:
    """Strips raw inline citations and footnote brackets, returning clean external-ready prose and discovered tokens."""
    collected: List[str] = []
    for tok in RE_ASSERTION_ID.findall(text) + RE_NODE_ID.findall(text):
        if tok not in collected:
            collected.append(tok)

    def _clean_link(m: re.Match[str]) -> str:
        label = m.group(1).strip("`")
        target = m.group(2)
        if RE_ASSERTION_ID.search(label) or RE_ASSERTION_ID.search(target):
            if not re.sub(RE_ASSERTION_ID, "", label).strip("` "):
                return ""
        if RE_NODE_ID.search(label):
            return _humanize_token(label)
        return label

    cleaned = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _clean_link, text)
    cleaned = re.sub(r"\s*\[\s*`?(?:workstream_|sow_|model_|pattern_|stakeholder_|person_|ast_|asrt_|assertion-|claim_|ev_)[^\]]*`?\s*\]", "", cleaned)
    cleaned = re.sub(r"\(\s*\[?\s*`?(?:workstream_|sow_|model_|pattern_|stakeholder_|person_|ast_|asrt_|assertion-|claim_|ev_)[^)]*?\)?\s*\)", "", cleaned)
    cleaned = re.sub(r"\(\s*(?:`?[a-zA-Z0-9_\-]+`?\s*,\s*)*`?[a-zA-Z0-9_\-]+`?\s*\)",
                     lambda m: "" if (RE_ASSERTION_ID.search(m.group(0)) or RE_NODE_ID.search(m.group(0))) else m.group(0), cleaned)
    cleaned = re.sub(r"\(\s*(?:,\s*)*\)", "", cleaned)
    cleaned = RE_ASSERTION_ID.sub("", cleaned)
    cleaned = RE_NODE_ID.sub(lambda m: _humanize_token(m.group(0)), cleaned)
    cleaned = re.sub(r"\s*\[\s*\d+(?:\s*,\s*\d+)*\s*\]", "", cleaned)
    cleaned = re.sub(r"\s+([,\.\;\:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return _simplify_note_readability(cleaned).strip(), collected


def format_seamless_query_response(
    raw_response: str, graph_path: Optional[str] = None
) -> SeamlessQueryResponse:
    """Transforms raw query markdown into external-ready prose with consolidated Graph Traversal Summary footer."""
    existing_footer: Optional[str] = None
    hdr = RE_LINEAGE_HEADER.search(raw_response)
    if hdr:
        prose_part = raw_response[: hdr.start()].rstrip().rstrip("-").rstrip()
        existing_footer = raw_response[hdr.start() :]
    else:
        prose_part = raw_response

    cleaned_prose, tokens = _clean_narrative_block(prose_part)

    # If no tokens discovered in prose, recover tokens from existing footer
    if not tokens and existing_footer:
        found = RE_ASSERTION_ID.findall(existing_footer) + RE_NODE_ID.findall(existing_footer)
        tokens = list(dict.fromkeys(found))

    footer, items, index_map = summarize_graph_traversal_coverage(tokens, graph_path=graph_path)

    if footer:
        formatted = f"{cleaned_prose.rstrip()}\n\n{footer}\n"
    else:
        formatted = f"{cleaned_prose.rstrip()}\n" if cleaned_prose else ""

    return SeamlessQueryResponse(
        clean_prose=cleaned_prose.strip(),
        lineage_footer=footer,
        formatted_markdown=formatted,
        citation_index_map=index_map,
        citations=items,
    )


def validate_seamless_prose(text: str) -> Tuple[bool, List[str]]:
    """Validates that narrative prose contains no raw database IDs, hashes, or inline footnote brackets."""
    violations: List[str] = []
    hdr = RE_LINEAGE_HEADER.search(text)
    narrative = text[: hdr.start()] if hdr else text

    raw_assertions = RE_ASSERTION_ID.findall(narrative)
    if raw_assertions:
        violations.append(
            f"Narrative prose contains {len(raw_assertions)} raw assertion ID(s): "
            f"{', '.join(sorted(list(set(raw_assertions)))[:5])}"
        )

    raw_nodes = RE_NODE_ID.findall(narrative)
    if raw_nodes:
        violations.append(
            f"Narrative prose contains {len(raw_nodes)} raw node ID(s): "
            f"{', '.join(sorted(list(set(raw_nodes)))[:5])}"
        )

    raw_uris = RE_FILE_URI.findall(narrative)
    if raw_uris:
        violations.append(
            f"Narrative prose contains {len(raw_uris)} raw file:// URI(s) in body text."
        )

    raw_footnotes = re.findall(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]", narrative)
    if raw_footnotes:
        violations.append(
            f"Narrative prose contains {len(raw_footnotes)} inline footnote marker(s) ({raw_footnotes[:3]}...); "
            f"prose must be clean for external sharing."
        )

    return len(violations) == 0, violations


def _collect_guard_violations(raw_md: str, fmt_md: str) -> list[str]:
    from dual_pipeline.grounding.metrics import (
        check_footnote_clause_entailment, check_sycophancy_and_meta_commentary,
    )
    return list(dict.fromkeys(
        check_sycophancy_and_meta_commentary(raw_md) + check_footnote_clause_entailment(raw_md)
        + check_sycophancy_and_meta_commentary(fmt_md) + check_footnote_clause_entailment(fmt_md)
    ))


def _resolve_raw_markdown(res: dict[str, Any], inner: dict[str, Any]) -> str:
    raw_md = res.get("markdown") or inner.get("markdown")
    if raw_md:
        return str(raw_md)
    ans = res.get("answer") or inner.get("answer")
    if isinstance(ans, dict):
        from dual_pipeline.query_core.reasoning import render_markdown
        return render_markdown(ans, res.get("x3_outcome") or inner.get("x3_outcome", "UNKNOWN"))
    return ""


def apply_seamless_lineage_formatting(res: Any, graph_path: Any | None = None) -> None:
    """Applies seamless lineage formatting, anti-sycophancy and footnote entailment checks to query results."""
    if not isinstance(res, dict):
        return
    inner = res.get("result") if isinstance(res.get("result"), dict) else {}
    raw_md = _resolve_raw_markdown(res, inner)
    if not raw_md:
        return
    fmt = format_seamless_query_response(raw_md, graph_path=str(graph_path) if graph_path else None)
    violations = _collect_guard_violations(raw_md, fmt.formatted_markdown)
    for target in (res, inner) if inner else (res,):
        target["formatted_markdown"] = fmt.formatted_markdown
        target["clean_prose"] = fmt.clean_prose
        target["lineage_footer"] = fmt.lineage_footer
        target["delivery_guard_status"] = "BLOCKED" if violations else "CERTIFIED"
        if violations:
            target["delivery_guard_violations"] = violations
