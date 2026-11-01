"""
dual_pipeline/hitl_governance_core/decision_clearance_core/plan_parser.py

Markdown plan and decision parsing logic for decision clearance.
Extracts decisions from tables, markdown sections, JSON blocks, and no-decision attestations.
Fails closed on malformed input, partial parse errors, or duplicate decision IDs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import NoDecisionAttestation, PreWaveDecision, ScopedDecision


def parse_confidence_pairs(options_text: str) -> Tuple[float, float]:
    """Extract top two confidence percentages or scores from an option text snippet."""
    pct_matches = re.findall(r"(\d+(?:\.\d+)?)\s*%", options_text)
    if len(pct_matches) >= 2:
        vals = sorted([float(x) / 100.0 for x in pct_matches], reverse=True)
        return vals[0], vals[1]
    if len(pct_matches) == 1:
        val = float(pct_matches[0]) / 100.0
        return val, 0.0
    return 0.50, 0.50


def _parse_table_option_splits(opts_text: str, top_conf: float, sec_conf: float) -> List[Dict[str, Any]]:
    opt_splits = re.split(r"\s+vs\s+", opts_text, flags=re.IGNORECASE)
    parsed_opts: List[Dict[str, Any]] = []
    for o_idx, opt_str in enumerate(opt_splits):
        is_rec = (o_idx == 0)
        pct_m = re.search(r"(\d+(?:\.\d+)?)\s*%", opt_str)
        score = (float(pct_m.group(1)) / 100.0) if pct_m else (top_conf if is_rec else sec_conf)
        parsed_opts.append({
            "label": opt_str.strip(),
            "plain_english_summary": opt_str.strip(),
            "impact_statement": "Ensures predictable operational execution while controlling risk.",
            "confidence_score": score,
            "recommended": is_rec,
        })
    return parsed_opts


def _parse_table_row(
    parts: List[str],
    idx: int,
    source_file: Optional[str],
) -> Optional[ScopedDecision]:
    if len(parts) < 4:
        return None
    d_id = parts[0]
    d_desc = parts[1]
    opts_text = parts[2]
    handling = parts[4] if len(parts) >= 5 else parts[3]

    wave_m = re.search(r"Wave\s*(\d+)", d_desc, re.IGNORECASE)
    wave_num = int(wave_m.group(1)) if wave_m else None
    top_conf, sec_conf = parse_confidence_pairs(opts_text)
    parsed_opts = _parse_table_option_splits(opts_text, top_conf, sec_conf)

    return ScopedDecision(
        decision_id=d_id,
        title=d_desc,
        wave=wave_num,
        top_confidence=top_conf,
        second_confidence=sec_conf,
        handling=handling,
        status="OPEN",
        question=f"How should {d_desc} be handled?",
        options=parsed_opts,
        source_file=source_file,
        source_line=idx,
    )


def parse_decisions_from_table(content: str, source_file: Optional[str] = None) -> List[ScopedDecision]:
    """Parse decision table rows in markdown (e.g. | ID | Decision | Options (confidence) | Δ | Handling |)."""
    decisions: List[ScopedDecision] = []
    lines = content.splitlines()
    table_header_re = re.compile(r"\|\s*ID\s*\|\s*Decision\s*\|\s*Options", re.IGNORECASE)
    in_table = False

    for idx, line in enumerate(lines, start=1):
        if table_header_re.search(line):
            in_table = True
            continue

        if not in_table:
            continue

        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            continue
        if "---" in stripped:
            continue

        parts = [p.strip() for p in stripped.split("|")[1:-1]]
        row_dec = _parse_table_row(parts, idx, source_file)
        if row_dec:
            decisions.append(row_dec)

    return decisions


def _parse_markdown_json_blocks(content: str) -> Tuple[List[PreWaveDecision], List[str]]:
    decisions: List[PreWaveDecision] = []
    errors: List[str] = []
    json_block_re = re.compile(r"```(?:json)?[:\-](?:deferred[-_]decisions|decisions|hitl)\s*\n(.*?)```", re.DOTALL)

    for m in json_block_re.finditer(content):
        block_text = m.group(1).strip()
        try:
            data = json.loads(block_text)
            if not isinstance(data, list):
                errors.append(f"Malformed decisions JSON block: expected list of objects, got {type(data).__name__}")
                continue
            for item in data:
                if not isinstance(item, dict) or "decision_id" not in item:
                    errors.append(f"Malformed decision item in JSON block: missing decision_id in {item}")
                    continue
                status_val = str(item.get("status", "OPEN")).strip().upper()
                decisions.append(PreWaveDecision(
                    decision_id=str(item["decision_id"]).strip(),
                    wave=int(item.get("wave", 1)),
                    status=status_val,
                    question=str(item.get("question", "Authorize decision?")).strip(),
                    options=list(item.get("options", [])),
                    category=str(item.get("category", "ARCHITECTURAL")).strip(),
                    plan_id=item.get("plan_id"),
                    target_files=list(item.get("target_files", [])),
                    action_description=item.get("action_description"),
                    metadata=dict(item.get("metadata", {})),
                ))
        except Exception as exc:
            errors.append(f"JSON parse error in decision block: {exc}")

    return decisions, errors


def _parse_markdown_section_decision(m: re.Match[str]) -> Optional[PreWaveDecision]:
    dec_id = m.group(1).strip()
    title = m.group(2).strip()
    body = m.group(3)

    wave_m = re.search(r"[-*]\s+\*\*Wave\*\*:\s*(\d+)", body, re.IGNORECASE)
    wave_num = int(wave_m.group(1)) if wave_m else 1

    q_m = re.search(r"[-*]\s+\*\*Question\*\*:\s*([^\n]+)", body, re.IGNORECASE)
    question_val = q_m.group(1).strip() if q_m else f"How should {title} be resolved?"

    opts: List[Dict[str, Any]] = []
    opt_matches = re.findall(r"[-*]\s+\*\*Option\s+([A-Z0-9]+)\*\*:\s*([^\n]+)", body, re.IGNORECASE)
    for opt_id, opt_text in opt_matches:
        opts.append({
            "label": f"Option {opt_id}: {opt_text.strip()}",
            "plain_english_summary": opt_text.strip(),
            "impact_statement": f"Adopts Option {opt_id} to resolve {title}.",
            "confidence_score": 0.50,
            "recommended": (opt_id == "A"),
        })

    if not opts:
        return None

    status_m = re.search(r"[-*]\s+\*\*Status\*\*:\s*([A-Za-z_]+)", body, re.IGNORECASE)
    status_val = status_m.group(1).strip().upper() if status_m else "OPEN"

    return PreWaveDecision(
        decision_id=dec_id,
        wave=wave_num,
        status=status_val,
        question=question_val,
        options=opts,
        action_description=title,
    )


def parse_decisions_from_markdown(content: str, source_path: Optional[Path] = None) -> Tuple[List[PreWaveDecision], List[str]]:
    """Parses deferred decisions embedded in markdown plan files (JSON blocks and sections)."""
    decisions, errors = _parse_markdown_json_blocks(content)
    sec_re = re.compile(r"###\s+Decision\s+([A-Za-z0-9_\-]+)[:\s]+([^\n]+)\n(.*?)(?=\n###|\Z)", re.DOTALL)

    for m in sec_re.finditer(content):
        parsed = _parse_markdown_section_decision(m)
        if parsed:
            decisions.append(parsed)

    return decisions, errors


def parse_no_decision_attestations(content: str, plan_path: Optional[str] = None) -> List[NoDecisionAttestation]:
    """Parses explicit zero-decision attestations declared in plan markdown."""
    attestations: List[NoDecisionAttestation] = []
    # Pattern 1: HTML comment comment <!-- no-decisions: wave=N reason="..." -->
    comment_re = re.compile(r"<!--\s*no-decisions:\s*wave=(\d+)\s+reason=[\"']([^\"']+)[\"']\s*-->", re.IGNORECASE)
    for m in comment_re.finditer(content):
        attestations.append(NoDecisionAttestation(
            wave=int(m.group(1)),
            reason=m.group(2).strip(),
            attested_by="html_comment",
            plan_path=plan_path,
        ))

    # Pattern 2: Section header ### No Decisions In Scope (Wave N)
    sec_re = re.compile(r"###\s+No\s+Decisions(?:\s+In\s+Scope)?\s*\(Wave\s*(\d+)\)\s*\n([^\n#]+)", re.IGNORECASE)
    for m in sec_re.finditer(content):
        attestations.append(NoDecisionAttestation(
            wave=int(m.group(1)),
            reason=m.group(2).strip(),
            attested_by="section_header",
            plan_path=plan_path,
        ))

    return attestations


def validate_decision_collection(decisions: Sequence[Any]) -> Tuple[bool, List[str]]:
    """Checks for duplicate decision IDs and empty identifiers in a collection."""
    seen_ids: Dict[str, int] = {}
    errors: List[str] = []

    for d in decisions:
        dec_id = getattr(d, "decision_id", None) or (d.get("decision_id") if isinstance(d, dict) else "")
        dec_id = str(dec_id).strip()
        if not dec_id:
            errors.append("MALFORMED_INPUT: Encountered decision with empty decision_id.")
            continue
        seen_ids[dec_id] = seen_ids.get(dec_id, 0) + 1

    for dec_id, count in seen_ids.items():
        if count > 1:
            errors.append(f"DUPLICATE_DECISION_ID: Decision ID '{dec_id}' appears {count} times in scope.")

    return len(errors) == 0, errors


def discover_implementation_plans(search_root: Path) -> List[Path]:
    """Auto-discover all wave-based implementation plans in workspace and documentation."""
    plans: List[Path] = []
    docs_plans = search_root / "docs" / "plans"
    if docs_plans.is_dir():
        for p in sorted(docs_plans.glob("WAVE_PLAN_*.md")):
            plans.append(p)

    root_plan = search_root / "implementation_plan.md"
    if root_plan.is_file():
        plans.append(root_plan)

    doc_dir = search_root / "documentation"
    if doc_dir.is_dir():
        for p in sorted(doc_dir.glob("*_PLAN*.md")):
            if p not in plans:
                plans.append(p)

    return plans
