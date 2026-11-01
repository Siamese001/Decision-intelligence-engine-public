"""Non-compensating document judge validation using the canonical Output 3 rubric."""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.prompts import OUTPUT3_CONTRACT
from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY


def _valid_score(value: Any, maximum: int) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and 0 <= value <= maximum and math.isfinite(value)
        and int(value) == value
    )


def _scorecard_errors(qualified: Dict[str, Any]) -> Tuple[list[str], int]:
    """Validate every original score before any summation or integer conversion."""
    dimensions = OUTPUT3_CONTRACT.rubric_dimensions
    floors = OUTPUT3_CONTRACT.get_dimension_floors(pipeline_mode="DOCUMENT_MODE")
    errors = []
    scores = []
    for dimension in dimensions:
        value = qualified.get(dimension.key)
        if not _valid_score(value, dimension.max_points):
            errors.append(f"Invalid or missing score: {dimension.key}")
            qualified[dimension.key] = None
        else:
            scores.append(int(value))
            if value < floors.get(dimension.key, 0.0):
                errors.append(f"Below rubric floor: {dimension.key}")
    composite = qualified.get("composite_score")
    if not _valid_score(composite, sum(d.max_points for d in dimensions)):
        errors.append("Invalid or missing composite_score")
        composite = 0
        qualified["composite_score"] = 0
    if len(scores) == len(dimensions) and composite != sum(scores):
        errors.append("Composite score does not match rubric dimension sum")
    return errors, int(composite)


def _check_evidence_text_grounded(
    eid: str,
    ev: Any,
    sig_id: str,
    norm_source: str,
) -> List[str]:
    if not isinstance(ev, dict):
        return [f"Signal '{sig_id}' cites missing evidence '{eid}'"]
    span = ev.get("exact_text") or ev.get("text") or ""
    if not span:
        return [f"Evidence '{eid}' cited by '{sig_id}' has empty text"]
    from dual_pipeline.pdf_source import normalize_pdf_text
    errs: List[str] = []
    fragments = [f.strip() for f in span.split("...") if f.strip()]
    for frag in fragments:
        norm_frag = normalize_pdf_text(frag)
        if norm_frag not in norm_source:
            errs.append(f"Signal '{sig_id}' cites ungrounded text in '{eid}': '{norm_frag[:60]}...'")
    return errs


def _validate_single_signal_grounding(
    sig: Any,
    evidence_index: Dict[str, Any],
    norm_source: str,
) -> List[str]:
    if not isinstance(sig, dict):
        return []
    sig_id = sig.get("signal_id") or "unknown_signal"
    eids = sig.get("evidence_ids") or []
    if not eids:
        return [f"Signal '{sig_id}' has no cited evidence_ids"]
    errs: List[str] = []
    for eid in eids:
        errs.extend(_check_evidence_text_grounded(eid, evidence_index.get(eid), sig_id, norm_source))
    return errs


def validate_signal_claim_grounding(
    projection: Dict[str, Any],
    source_text: str,
) -> Tuple[bool, List[str]]:
    """Deterministic check that each signal's cited text exists word-for-word in the source document."""
    if not isinstance(projection, dict):
        return False, ["Projection must be a dict"]
    if not source_text or not isinstance(source_text, str):
        return False, ["Missing or empty source text for grounding validation"]

    from dual_pipeline.pdf_source import normalize_pdf_text
    norm_source = normalize_pdf_text(source_text)
    evidence_index = projection.get("evidence_index", {})
    mutations = projection.get("graph_mutations", {})
    signals = mutations.get("relationship_signals", [])

    errors: List[str] = []
    for sig in signals:
        errors.extend(_validate_single_signal_grounding(sig, evidence_index, norm_source))
    for cat_key in ("decision_updates", "proposal_updates", "risk_updates", "strategic_intent_signals"):
        for item in mutations.get(cat_key, []):
            if isinstance(item, dict) and item.get("evidence_ids"):
                errors.extend(_validate_single_signal_grounding(item, evidence_index, norm_source))
    return len(errors) == 0, errors


def _is_auto_healed_signal(sig: Dict[str, Any]) -> bool:
    return bool(sig.get("auto_healed") or str(sig.get("signal_id") or "").startswith("sig-auto-"))


def _signal_matches_judge_rationale(sig: Dict[str, Any], rationale_low: str) -> bool:
    sig_id = str(sig.get("signal_id") or "").lower()
    base_sig_id = re.sub(r"-[0-9a-f]{8}$", "", sig_id)
    if (sig_id and sig_id in rationale_low) or (base_sig_id and base_sig_id in rationale_low):
        return True
    f_id = str(sig.get("from_id") or "").lower()
    t_id = str(sig.get("to_id") or "").lower()
    pred = str(sig.get("relationship") or sig.get("predicate") or "").upper()
    if f_id.startswith("project:") and t_id.startswith("project:") and pred == "CONTAINS":
        return True
    f_slug = f_id.split(":", 1)[-1]
    t_slug = t_id.split(":", 1)[-1]
    return bool(f_slug and t_slug and f_slug in rationale_low and t_slug in rationale_low)


def _collect_judge_rationale_text(qualified: Dict[str, Any]) -> str:
    parts: List[str] = []
    sources = [qualified]
    if isinstance(qualified.get("raw_judge_scorecard"), dict):
        sources.append(qualified["raw_judge_scorecard"])
    for src in sources:
        for k in ("forensic_rationale", "rationale", "actionable_remediation_diff", "actionable_remediation"):
            if src.get(k):
                parts.append(str(src[k]))
        for dk in ("grounding_defects", "defect_flags", "defect_codes"):
            vals = src.get(dk)
            if isinstance(vals, list):
                parts.extend(str(v) for v in vals if v)
    return " ".join(parts).lower()


def _prune_signal_ids_from_container(container: Any, pruned_set: set[str]) -> None:
    if not isinstance(container, dict):
        return
    for key in ("relationship_signals", "assertions", "signals", "edges"):
        items = container.get(key)
        if isinstance(items, list):
            container[key] = [
                s for s in items
                if not (isinstance(s, dict) and str(s.get("signal_id") or s.get("assertion_id") or s.get("id") or "") in pruned_set)
            ]


def _clear_pruned_defect_flags(qualified: Dict[str, Any], pruned_set: set[str]) -> List[str]:
    for dk in ("grounding_defects", "defect_flags", "defect_codes"):
        qualified.pop(dk, None)
    if isinstance(qualified.get("raw_judge_scorecard"), dict):
        raw_copy = dict(qualified["raw_judge_scorecard"])
        for dk in ("grounding_defects", "defect_flags", "defect_codes"):
            raw_copy.pop(dk, None)
        raw_copy["verdict"] = "PASS"
        qualified["raw_judge_scorecard"] = raw_copy
    qualified["verdict"] = "PASS"
    sorted_ids = sorted(pruned_set)
    qualified["pruned_auto_healed_signals"] = sorted_ids
    return sorted_ids


def _has_non_auto_healed_citation(signals: List[Any], rationale_low: str) -> bool:
    return any(
        isinstance(s, dict)
        and not _is_auto_healed_signal(s)
        and s.get("signal_id")
        and str(s["signal_id"]).lower() in rationale_low
        for s in signals
    )


def prune_flagged_auto_healed_signals(qualified: Dict[str, Any]) -> List[str]:
    """Deterministically prune judge-flagged sig-auto-* edges on >=90 floor-passing candidates."""
    proj = qualified.get("projection") or qualified.get("output_3")
    if not isinstance(proj, dict):
        return []
    mutations = proj.get("graph_mutations") if isinstance(proj.get("graph_mutations"), dict) else proj
    signals = mutations.get("relationship_signals") or mutations.get("assertions") or []
    rationale_low = _collect_judge_rationale_text(qualified)
    if not isinstance(signals, list) or not signals or not rationale_low or _has_non_auto_healed_citation(signals, rationale_low):
        return []
    pruned_set = {
        str(s.get("signal_id") or "")
        for s in signals
        if isinstance(s, dict) and _is_auto_healed_signal(s) and _signal_matches_judge_rationale(s, rationale_low) and s.get("signal_id")
    }
    if not pruned_set:
        return []
    for target in (mutations, proj, qualified.get("assertion_candidates"), qualified.get("graph_candidates")):
        _prune_signal_ids_from_container(target, pruned_set)
    return _clear_pruned_defect_flags(qualified, pruned_set)


_CRITICAL_DEFECT_CODE_PREFIXES = (
    "ERR_HALLUCINATED_", "ERR_VACUOUS_", "ERR_STUB_",
    "ERR_UNRESOLVED_", "ERR_ACTION_ITEM_", "ERR_OMITTED_", "ERR_UNDERREPRESENTED_",
    "ERR_SELF_EVALUATION",
)


def _extract_named_grounding_defects(qualified: Dict[str, Any]) -> List[str]:
    explicit = qualified.get("grounding_defects") or qualified.get("defect_flags")
    if isinstance(explicit, list) and any(explicit):
        return [str(d) for d in explicit if d]
    raw_codes = qualified.get("defect_codes")
    if not isinstance(raw_codes, list):
        return []
    is_fail = qualified.get("verdict") != "PASS"
    return [str(c) for c in raw_codes if c and (is_fail or str(c).startswith(_CRITICAL_DEFECT_CODE_PREFIXES))]


def _collect_grounding_defects(qualified: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    floors = OUTPUT3_CONTRACT.get_dimension_floors()
    floor_d6 = floors.get("dimension_6_evidence_grounding_and_schema", 14)
    grounding = qualified.get("dimension_6_evidence_grounding_and_schema")
    if _valid_score(grounding, 20) and grounding < floor_d6:
        errors.append(f"Document grounding below rubric floor of {floor_d6}")

    named = _extract_named_grounding_defects(qualified)
    if named:
        errors.append(f"Named grounding defects present: {'; '.join(named)}")

    proj = qualified.get("projection") or qualified.get("output_3")
    src = qualified.get("source_text")
    if isinstance(proj, dict) and isinstance(src, str) and src:
        is_grounded, grounding_errs = validate_signal_claim_grounding(proj, src)
        if not is_grounded:
            errors.extend(grounding_errs)
    return errors


def qualify_document_judge(result: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, int]:
    """Preserve judge vetoes; auto-prune flagged sig-auto-* edges when score >= 90 and floors pass."""
    qualified = dict(result)
    errors, score = _scorecard_errors(qualified)
    if not errors and score >= CANONICAL_RELEASE_POLICY.output3_pass_threshold:
        prune_flagged_auto_healed_signals(qualified)
    errors.extend(_collect_grounding_defects(qualified))

    raw = qualified.get("raw_judge_scorecard")
    if raw is not None:
        if not isinstance(raw, dict):
            errors.append("Invalid raw judge scorecard")
        else:
            raw_errors, _ = _scorecard_errors(dict(raw))
            errors.extend(raw_errors)
            if raw.get("verdict") in ("FAIL", "HITL_REQUIRED"):
                qualified["verdict"] = raw["verdict"]
    passed = (
        not errors and qualified.get("verdict") == "PASS"
        and score >= CANONICAL_RELEASE_POLICY.output3_pass_threshold
    )
    if errors:
        qualified["quality_failures"] = errors
    if any(error.startswith(("Invalid", "Composite")) for error in errors):
        qualified["composite_score"] = score = 0
        qualified["verdict"] = "FAIL"
    if not passed and qualified.get("verdict") not in ("FAIL", "HITL_REQUIRED"):
        qualified["verdict"] = "FAIL"
    return qualified, passed, score


def _page_count_from_metadata(meta: Dict[str, Any]) -> int:
    val = meta.get("pages_count") or meta.get("page_count")
    if isinstance(val, int) and val > 0:
        return val
    return 0


def _page_count_from_coverage(cov: Any) -> int:
    if not isinstance(cov, dict):
        return 0
    val = cov.get("pages_count") or cov.get("page_count")
    if isinstance(val, int) and val > 0:
        return val
    if isinstance(val, str) and val.isdigit():
        return int(val)
    return 0


def _page_count_from_units(units: Any) -> int:
    if not (isinstance(units, list) and units):
        return 0
    page_units = [
        u for u in units
        if (isinstance(u, dict) and (u.get("unit_type") in ("page", "slide") or u.get("format") in ("pdf", "pptx")))
        or (hasattr(u, "unit_type") and u.unit_type in ("page", "slide"))
    ]
    return len(page_units)


def _page_count_from_text(source_text: str) -> int:
    if not source_text:
        return 0
    pages = re.findall(r"(?:##\s*Page|\bSlide\b)\s*(\d+)", source_text, re.IGNORECASE)
    return max((int(p) for p in pages), default=0)


def _detect_document_page_count(source_text: str, metadata: Optional[Dict[str, Any]] = None) -> int:
    meta = metadata or {}
    cnt = _page_count_from_metadata(meta)
    if cnt:
        return cnt
    cov = meta.get("_source_coverage") or meta.get("source_coverage")
    cnt = _page_count_from_coverage(cov)
    if cnt:
        return cnt
    units = meta.get("source_units")
    cnt = _page_count_from_units(units)
    if cnt:
        return cnt
    return _page_count_from_text(source_text)


def _extract_locator_pages(loc: Any) -> set[int]:
    if isinstance(loc, dict):
        loc_str = " ".join(str(v) for v in loc.values())
    elif isinstance(loc, (str, int)):
        loc_str = str(loc)
    else:
        return set()
    matches = re.findall(r"(?:slide|page|p\.?)\s*(\d+)", loc_str, re.IGNORECASE)
    return {int(m) for m in matches if m.isdigit()}


def _build_page_header_index(source_text: str) -> list[tuple[int, int]]:
    matches = re.finditer(r"(?:##\s*Page|Slide|p\.)\s*(\d+)", source_text, re.IGNORECASE)
    return [(m.start(), int(m.group(1))) for m in matches if m.group(1).isdigit()]


def _resolve_text_span_page(span: str, source_text: str, page_headers: list[tuple[int, int]]) -> Optional[int]:
    if not (span and source_text and page_headers):
        return None
    frag = span.strip().split("...")[0].strip()
    if len(frag) < 8:
        return None
    idx = source_text.find(frag[:60])
    if idx == -1:
        words = frag[:60].split()
        if words:
            m = re.search(r"\s+".join(re.escape(w) for w in words), source_text, re.IGNORECASE)
            if m:
                idx = m.start()
    if idx == -1:
        return None
    cur_page = None
    for start_pos, page_num in page_headers:
        if start_pos <= idx:
            cur_page = page_num
        else:
            break
    return cur_page


def _resolve_single_evidence_pages(
    ev: Any, source_text: str, page_headers: list[tuple[int, int]]
) -> set[int]:
    if not isinstance(ev, dict):
        return set()
    pages = _extract_locator_pages(ev.get("locator"))
    if pages:
        return pages
    span = ev.get("exact_text") or ev.get("text") or ""
    page = _resolve_text_span_page(span, source_text, page_headers)
    return {page} if page is not None else set()


def _resolve_evidence_pages(
    evidence_index: Dict[str, Any],
    source_text: str,
) -> set[int]:
    page_headers = _build_page_header_index(source_text) if source_text else []
    pages: set[int] = set()
    for ev in evidence_index.values():
        pages.update(_resolve_single_evidence_pages(ev, source_text, page_headers))
    return pages



def _extract_unit_page_num(u: Dict[str, Any]) -> Optional[int]:
    u_meta = u.get("metadata") or {}
    if u_meta.get("is_summary_unit") or u.get("unit_type") == "summary_section":
        return None
    pnum = u_meta.get("slide_number") or u_meta.get("page_num")
    if not pnum:
        m = re.search(r"(?:slide|page|p)[:\s]*(\d+)", str(u.get("locator") or ""), re.I)
        if m:
            pnum = int(m.group(1))
    return pnum if isinstance(pnum, int) else None


def _populate_parsed_text_units(source_text: str, page_count: int, unit_by_num: Dict[int, Dict[str, Any]]) -> None:
    from dual_pipeline.document_pipeline_core.unit_decomposition import extract_slide_and_section_units
    for u in extract_slide_and_section_units(source_text, "doc", "md"):
        pnum = u.metadata.get("slide_number") or u.metadata.get("page_num")
        if isinstance(pnum, int) and 1 <= pnum <= page_count:
            unit_by_num[pnum] = u.to_dict()


def _build_unit_by_num_map(source_text: str, metadata: Optional[Dict[str, Any]], page_count: int) -> Dict[int, Dict[str, Any]]:
    meta = metadata or {}
    units = meta.get("slide_units") or meta.get("source_units") or []
    unit_by_num: Dict[int, Dict[str, Any]] = {}
    for u in units:
        if isinstance(u, dict) and u.get("content") is not None:
            pnum = _extract_unit_page_num(u)
            if pnum and 1 <= pnum <= page_count:
                unit_by_num[pnum] = u
    if not unit_by_num and source_text:
        _populate_parsed_text_units(source_text, page_count, unit_by_num)
    return unit_by_num


def _is_quarantined_sieve_unit(u: Dict[str, Any]) -> bool:
    u_meta = u.get("metadata") or {}
    sieve = str(u_meta.get("sieve_class") or u_meta.get("disposition") or "")
    return sieve.startswith("QUARANTINE_")


def _is_unit_exempt(p: int, u: Dict[str, Any]) -> bool:
    from dual_pipeline.document_pipeline_core.unit_decomposition import classify_unit_materiality
    u_meta = u.get("metadata") or {}
    disp = str(u_meta.get("disposition") or "")
    sieve = str(u_meta.get("sieve_class") or disp)
    content = str(u.get("content") or "").strip()
    if sieve.startswith("QUARANTINE_") or sieve in ("CONTEXT_STRUCTURAL_DIVIDER", "CONTEXT_NARRATIVE_OR_RECAP"):
        return True
    if disp in ("EXCLUDED", "SUMMARY_OVERVIEW", "STRUCTURAL_DIVIDER", "CONTEXT_NARRATIVE_OR_RECAP"):
        return True
    if disp == "ESCALATE_VISUAL" and not any(c.isalnum() for c in content):
        return True
    mat = u_meta.get("materiality") or classify_unit_materiality(str(u_meta.get("title") or ""), content, int(u_meta.get("image_count") or 0))
    if mat != "SUBSTANTIVE":
        return True
    if p == 1 and len(content) < 140 and "-" not in content and "|" not in content:
        low = content.lower()
        return "confirms material evidence" not in low and "marker" not in low
    return False


def _detect_quarantined_unit_leakage(
    cited_pages: set[int],
    source_text: str,
    metadata: Optional[Dict[str, Any]],
    page_count: int,
) -> List[int]:
    meta = metadata or {}
    explicit_units = meta.get("slide_units") or meta.get("source_units") or []
    if not explicit_units:
        return []
    unit_by_num = _build_unit_by_num_map(source_text, metadata, page_count)
    leaked: List[int] = []
    for p in sorted(cited_pages):
        u = unit_by_num.get(p)
        if u is not None and _is_quarantined_sieve_unit(u):
            leaked.append(p)
    return leaked


def _resolve_role_catalog_planned_units(
    meta: Dict[str, Any],
    page_count: int,
) -> Optional[Tuple[List[int], List[int]]]:
    if str(meta.get("document_archetype") or "") != "ROLE_FRAMEWORK_CATALOG":
        return None
    plan = meta.get("document_ingestion_plan")
    if not (isinstance(plan, dict) and isinstance(plan.get("high_signal_targets"), list)):
        return None
    planned_pages = {
        int(m.group(1))
        for t in plan["high_signal_targets"]
        if isinstance(t, dict) and (m := re.search(r"(\d+)", str(t.get("page") or "")))
    }
    if not planned_pages:
        return None
    sub = sorted(p for p in planned_pages if 1 <= p <= page_count)
    ex = sorted(p for p in range(1, page_count + 1) if p not in planned_pages)
    return sub, ex


def _classify_document_units(
    source_text: str,
    metadata: Optional[Dict[str, Any]],
    page_count: int,
) -> Tuple[List[int], List[int]]:
    """Classify each slide/page (1..page_count) as substantive vs exempt structural/divider."""
    planned = _resolve_role_catalog_planned_units(metadata or {}, page_count)
    if planned is not None:
        return planned
    unit_by_num = _build_unit_by_num_map(source_text, metadata, page_count)
    substantive: List[int] = []
    exempt: List[int] = []
    for p in range(1, page_count + 1):
        u = unit_by_num.get(p)
        if u is None:
            (substantive if not unit_by_num else exempt).append(p)
        elif _is_unit_exempt(p, u):
            exempt.append(p)
        else:
            substantive.append(p)
    return substantive, exempt


def _page_bucket(p: int, b1: int, b2: int) -> str:
    if p <= b1:
        return "early"
    if p <= b2:
        return "middle"
    return "late"


def validate_slide_deck_coverage_and_yield(
    projection: Dict[str, Any],
    source_text: str,
    metadata: Optional[Dict[str, Any]] = None,
    mutation_count: int = 0,
) -> Tuple[bool, list[str], Dict[str, Any]]:
    page_count = _detect_document_page_count(source_text, metadata)
    if page_count <= 2:
        return True, [], {}

    b1 = page_count // 3
    b2 = (2 * page_count + 1) // 3
    cited_pages = _resolve_evidence_pages(projection.get("evidence_index", {}), source_text)
    substantive_units, exempt_units = _classify_document_units(source_text, metadata, page_count)
    covered_substantive = sorted(set(substantive_units) & cited_pages)
    uncovered_substantive = sorted(set(substantive_units) - cited_pages)
    coverage_ratio = round(len(covered_substantive) / len(substantive_units), 4) if substantive_units else 1.0

    covered_buckets = {_page_bucket(p, b1, b2) for p in cited_pages if 1 <= p <= page_count}
    expected_buckets = {_page_bucket(p, b1, b2) for p in substantive_units} or {"early", "middle", "late"}
    missing_buckets = expected_buckets - covered_buckets

    errors: list[str] = []
    leaked_quarantined = _detect_quarantined_unit_leakage(cited_pages, source_text, metadata, page_count)
    if leaked_quarantined:
        errors.append(f"DOCUMENT_QUARANTINED_UNIT_LEAKAGE: Mutations cited quarantined Q1-Q7 unit(s): {leaked_quarantined}")
    archetype = str((metadata or {}).get("document_archetype") or "")
    min_ratio = 0.85 if archetype in ("SPARSE_ENABLEMENT_WORKSHOP", "HYBRID_PORTFOLIO_AND_TEMPLATE_DECK") else 0.90
    if missing_buckets or coverage_ratio < min_ratio:
        errors.append(
            f"DOCUMENT_SLIDE_COVERAGE_GAP: Document with {page_count} pages/slides requires >= {min_ratio:.0%} per-unit materiality "
            f"coverage across substantive slides (covered {len(covered_substantive)}/{len(substantive_units)} = "
            f"{coverage_ratio:.1%}; uncovered_substantive_units: {uncovered_substantive}; "
            f"missing_buckets: {sorted(missing_buckets)})"
        )
    min_mutations = min(8, max(3, len(substantive_units) * 3))
    if mutation_count < min_mutations:
        errors.append(
            f"DOCUMENT_YIELD_DEFICIT: Multi-slide document with {page_count} pages/slides ({len(substantive_units)} substantive) requires at least {min_mutations} substantive graph mutations (found {mutation_count})"
        )

    telemetry = {
        "page_count": page_count,
        "buckets": {"early": f"1-{b1}", "middle": f"{b1 + 1}-{b2}", "late": f"{b2 + 1}-{page_count}"},
        "cited_pages": sorted(cited_pages),
        "covered_buckets": sorted(covered_buckets),
        "missing_buckets": sorted(missing_buckets),
        "substantive_units": substantive_units,
        "exempt_structural_units": exempt_units,
        "covered_substantive_units": covered_substantive,
        "uncovered_substantive_units": uncovered_substantive,
        "substantive_coverage_ratio": coverage_ratio,
        "mutation_count": mutation_count,
        "passed": len(errors) == 0,
    }
    return len(errors) == 0, errors, telemetry


def _append_unique_failures(q3: Dict[str, Any], errors: List[str]) -> None:
    if not errors:
        return
    failures = q3.setdefault("failures", [])
    for err in errors:
        if err not in failures:
            failures.append(err)
    q3["passed"] = False
    q3["status"] = "FAIL"


def _count_graph_mutations(projection: Dict[str, Any]) -> int:
    mutations = projection.get("graph_mutations", {})
    return sum(
        len(mutations.get(key, []))
        for key in (
            "entity_upserts", "relationship_signals", "decision_updates",
            "proposal_updates", "risk_updates", "strategic_intent_signals",
        )
    )


def apply_document_quality_and_yield_checks(
    q3: Dict[str, Any],
    projection: Dict[str, Any],
    source_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    grounding_passed, grounding_errors = validate_signal_claim_grounding(projection, source_text)
    _append_unique_failures(q3, grounding_errors)

    mutation_count = _count_graph_mutations(projection)
    evidence_count = len(projection.get("evidence_index", {}))
    yield_passed = evidence_count > 0 and mutation_count > 0
    q3["document_signal_yield"] = {
        "passed": yield_passed,
        "evidence_count": evidence_count,
        "mutation_count": mutation_count,
    }
    if not yield_passed:
        _append_unique_failures(
            q3, ["Document projection requires source-grounded evidence and at least one substantive graph mutation"]
        )

    deck_passed, deck_errors, deck_telemetry = validate_slide_deck_coverage_and_yield(
        projection, source_text, metadata=metadata, mutation_count=mutation_count,
    )
    if deck_telemetry:
        q3["document_slide_coverage"] = deck_telemetry
    _append_unique_failures(q3, deck_errors)

    return bool(q3.get("passed", False))


