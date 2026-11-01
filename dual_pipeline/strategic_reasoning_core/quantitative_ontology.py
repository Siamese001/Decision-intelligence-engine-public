"""
dual_pipeline/strategic_reasoning_core/quantitative_ontology.py

Typed quantitative constraint extraction, literal type inference, and constraint categorization.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_CONSTRAINT_CLAIM_RE = re.compile(
    r"\b(?:qualification\s+bar|pass\s+rate|phasing\s+out|segregated\s+pods|deprovisioning|"
    r"two\s+weeks\s+prior|14[- ]day|3\s+days\s+per\s+week|three\s+days\s+per\s+week|"
    r"45%\s+higher|1\s*:\s*8|one\s+to\s+eight|engineers\s+per\s+workstream|capped\s+at|"
    r"gain[- ]sharing|game\s+sharing|42[- ]hours?\s+a\s+week|42[- ]hour\s+minimum)\b",
    re.IGNORECASE,
)

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "talent_bar",
        re.compile(
            r"\b(?:qualification\s+bar|pass\s+rate|screening|candidates?|7\s+years|seven\s+years|"
            r"re[- ]screening|anti[- ]patterns?|talent\s+pool|bar[- ]cleared)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "capacity_scaling",
        re.compile(
            r"\b(?:1\s*:\s*8|one\s+to\s+eight|ratio|engineers?\s+per\s+workstream|"
            r"segregated\s+pods?|shared\s+domain\s+pods?|absorption|headcount|pods?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "commercial_pricing",
        re.compile(
            r"\b(?:45%\s+higher|rate\s+card|42[- ]hour|42\s+hours|outcome[- ]based|"
            r"gain[- ]sharing|fixed[- ]fee|loss\s+leader|quarterly\s+burn|\$\d+|margin|sow\s+pricing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "schedule_notice",
        re.compile(
            r"\b(?:deprovisioning|two\s+weeks\s+prior|14[- ]day|notice\s+window|"
            r"staggered\s+sow|rolloff|expiration|ramp\s+weeks?|lead\s+time)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "operational_continuity",
        re.compile(
            r"\b(?:3\s+days\s+per\s+week|three\s+days\s+per\s+week|dedicated\s+seating|"
            r"fieldglass|badging|turnovers?|single[- ]threaded|co[- ]located)\b",
            re.IGNORECASE,
        ),
    ),
)

_RATIO_RE = re.compile(r"(?<![:\d])\b(?P<num>[1-9]\d*)\s*:\s*(?P<den>[1-9]\d*)\b(?![:\d])")
_PCT_RANGE_RE = re.compile(r"\b(?P<lo>\d+(?:\.\d+)?)\s*%?\s*(?:[-\u2013\u2014]|to|and)\s*(?P<hi>\d+(?:\.\d+)?)\s*%")
_PCT_SINGLE_RE = re.compile(r"\b(?P<val>\d+(?:\.\d+)?)\s*%")
_CURRENCY_RE = re.compile(
    r"\$(?P<val>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<mag>[km])?(?:\s*/\s*(?P<per>hr|hour|quarter|qtr|yr|year|month|mo))?",
    re.IGNORECASE,
)
_DAYS_WEEKS_RE = re.compile(
    r"\b(?P<val>\d+|two|three|six|fourteen)\s*[- ]?(?P<unit>day|days|week|weeks|month|months|year|years)\b",
    re.IGNORECASE,
)
_HOURS_WEEK_RE = re.compile(r"\b(?P<val>\d+)\s*[- ]?(?:hour|hr)s?(?:\s+(?:per|a)\s+week|\s+minimum)?\b", re.IGNORECASE)
_COUNT_RE = re.compile(
    r"\b(?P<lo>\d+)(?:\s*(?:[-\u2013\u2014]|to)\s*(?P<hi>\d+))?\s+"
    r"(?:(?:active|local|shared|platform|full[- ]stack|dedicated|segregated|wpb|multifaceted|lead)\s+)*"
    r"(?P<kind>pods?|engineers?|workstreams?|programs?)\b",
    re.IGNORECASE,
)

_WORD_NUMBERS: dict[str, float] = {
    "two": 2.0,
    "three": 3.0,
    "six": 6.0,
    "fourteen": 14.0,
}


def is_constraint_claim(text: str | None) -> bool:
    """Return True when claim text expresses an operational, commercial, or governance constraint."""
    return bool(text and _CONSTRAINT_CLAIM_RE.search(text))


def classify_constraint_category(text: str | None) -> str:
    """Classify a constraint claim into one of the five canonical decision constraint categories."""
    raw = str(text or "")
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(raw):
            return category
    return "general_constraint"


def _extract_ratios(raw: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for match in _RATIO_RE.finditer(raw):
        num, den = float(match.group("num")), float(match.group("den"))
        if num > 0 and den > 0:
            items.append({
                "metric_type": "headcount_ratio",
                "numeric_value": den / num,
                "min_value": num,
                "max_value": den,
                "unit": "engineers_per_anchor",
                "operator": "<=",
                "scope_qualifier": "capacity_scaling",
            })
    return items


def _extract_percentages(raw: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    is_bar = bool(re.search(r"pass\s+rate|qualification|screening|bar|candidate", raw, re.I))
    is_prem = bool(re.search(r"higher|premium|rate\s+card|cost|margin", raw, re.I))
    cat = classify_constraint_category(raw)
    pct_spans: list[tuple[int, int]] = []
    for match in _PCT_RANGE_RE.finditer(raw):
        lo, hi = float(match.group("lo")), float(match.group("hi"))
        pct_spans.append(match.span())
        items.append({
            "metric_type": "pass_rate_pct" if is_bar else "percentage_bound",
            "numeric_value": round((lo + hi) / 2.0, 4),
            "min_value": lo,
            "max_value": hi,
            "unit": "percent",
            "operator": "range",
            "scope_qualifier": "talent_bar" if is_bar else cat,
        })
    single_type = "rate_premium_pct" if is_prem else ("pass_rate_pct" if is_bar else "percentage_bound")
    single_op = ">=" if is_prem else "<="
    for match in _PCT_SINGLE_RE.finditer(raw):
        if any(start <= match.start() < end for start, end in pct_spans):
            continue
        val = float(match.group("val"))
        items.append({
            "metric_type": single_type,
            "numeric_value": val,
            "min_value": val,
            "max_value": val,
            "unit": "percent",
            "operator": single_op,
            "scope_qualifier": cat,
        })
    return items


_MAG_MULT = {"k": 1_000.0, "m": 1_000_000.0}
_PER_UNIT = {"hr": "usd_per_hour", "hour": "usd_per_hour", "quarter": "usd_per_quarter", "qtr": "usd_per_quarter"}
_UNIT_DAYS_MULT = {"day": 1.0, "week": 7.0, "month": 30.0, "year": 365.0}


def _extract_currencies_and_hours(raw: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for match in _CURRENCY_RE.finditer(raw):
        base = float(match.group("val").replace(",", ""))
        mult = _MAG_MULT.get((match.group("mag") or "").lower(), 1.0)
        scaled = round(base * mult, 2)
        unit = _PER_UNIT.get((match.group("per") or "total").lower(), "usd_total")
        items.append({
            "metric_type": "currency_rate",
            "numeric_value": scaled,
            "min_value": scaled,
            "max_value": scaled,
            "unit": unit,
            "operator": "<=",
            "scope_qualifier": "commercial_pricing",
        })
    for match in _HOURS_WEEK_RE.finditer(raw):
        val = float(match.group("val"))
        items.append({
            "metric_type": "weekly_billing_hours",
            "numeric_value": val,
            "min_value": val,
            "max_value": val,
            "unit": "hours_per_week",
            "operator": ">=",
            "scope_qualifier": "commercial_pricing",
        })
    return items


def _extract_durations(raw: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    is_notice = bool(re.search(r"deprovisioning|prior|notice|terminates", raw, re.I))
    cat = "schedule_notice" if is_notice else classify_constraint_category(raw)
    op = ">=" if is_notice else "=="
    for match in _DAYS_WEEKS_RE.finditer(raw):
        raw_val = match.group("val").lower()
        val = _WORD_NUMBERS.get(raw_val)
        if val is None:
            val = float(raw_val)
        unit_raw = match.group("unit").lower().rstrip("s")
        num_val = val * _UNIT_DAYS_MULT.get(unit_raw, 1.0) if is_notice else val
        items.append({
            "metric_type": "notice_window_days" if is_notice else f"duration_{unit_raw}s",
            "numeric_value": num_val,
            "min_value": num_val,
            "max_value": num_val,
            "unit": "days" if is_notice else f"{unit_raw}s",
            "operator": op,
            "scope_qualifier": cat,
        })
    return items


_KIND_METRIC = {"pod": "pod_count", "engineer": "engineer_headcount"}


def extract_quantitative_constraints(text: str | None) -> list[dict[str, Any]]:
    """Extract deterministic quantitative constraint parameters from claim or literal text."""
    raw = str(text or "").strip()
    if not raw:
        return []
    constraints = (
        _extract_ratios(raw)
        + _extract_percentages(raw)
        + _extract_currencies_and_hours(raw)
        + _extract_durations(raw)
    )
    for match in _COUNT_RE.finditer(raw):
        lo = float(match.group("lo"))
        hi = float(match.group("hi") or lo)
        kind = match.group("kind").lower().rstrip("s")
        constraints.append({
            "metric_type": _KIND_METRIC.get(kind, "active_workstreams"),
            "numeric_value": round((lo + hi) / 2.0, 2),
            "min_value": lo,
            "max_value": hi,
            "unit": f"{kind}s",
            "operator": "range" if hi > lo else "==",
            "scope_qualifier": "capacity_scaling",
        })
    return constraints


def infer_object_literal_type(text: str | None) -> str:
    """Infer a fine-grained literal type for qualifier metadata while keeping SQLite column text-safe."""
    extracted = extract_quantitative_constraints(text)
    if not extracted:
        return "text"
    first_type = str(extracted[0].get("metric_type") or "")
    if first_type == "headcount_ratio":
        return "numeric_ratio"
    if "pct" in first_type or first_type == "percentage_bound":
        return "percentage"
    if first_type == "currency_rate":
        return "currency_rate"
    if "days" in first_type or "duration_" in first_type or first_type == "weekly_billing_hours":
        return "duration_days"
    if first_type in {"pod_count", "engineer_headcount", "active_workstreams"}:
        return "headcount"
    return "text"


def hydrate_quantitative_metadata(assertion: Mapping[str, Any], path: dict[str, Any]) -> list[dict[str, Any]]:
    """Populate quantitative_constraints and constraint_category on a hydrated graph_path dict."""
    raw_qual = assertion["qualifiers_json"] if "qualifiers_json" in assertion.keys() else "{}"
    try:
        qualifiers = json.loads(raw_qual or "{}")
    except (TypeError, json.JSONDecodeError):
        qualifiers = {}
    text = str(assertion["object_literal"] or assertion["scope_text"] or "")
    quant = qualifiers.get("quantitative_constraints") if isinstance(qualifiers, dict) else None
    if not isinstance(quant, list) or not quant:
        quant = extract_quantitative_constraints(text)
    if quant:
        path["quantitative_constraints"] = quant
    if is_constraint_claim(text):
        path["constraint_category"] = classify_constraint_category(text)
    return quant


_AUDIENCE_CLAUSE_RE = re.compile(
    r"(?:^|\b)[Aa]udience\s*(?:is|:)?\s*"
    r"(?P<names>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?(?:\s*(?:,|\band\b|&)\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)*)"
    r"(?:\s+who\b[^.\n]*|\b)?"
)
_AUDIENCE_STOPWORDS = {
    "Audience", "Who", "Are", "Intimately", "Familiar", "With", "Topic",
    "Per", "Transcript", "Call", "Yesterday", "Need", "Leadership",
    "Creative", "Pov", "What", "Might", "Work", "Will", "Not", "Explain",
    "Why", "Intro", "Paragraph", "Then", "Table", "Words", "Provide",
    "Honest", "Analysis", "Perspective", "West", "Palm", "Beach",
}


def extract_audience_stakeholders(question: str) -> list[str]:
    """Extract named executive audience stakeholders from an 'Audience ...' clause."""
    if not question:
        return []
    match = _AUDIENCE_CLAUSE_RE.search(question)
    if not match:
        return []
    raw_names = match.group("names") or ""
    names: list[str] = []
    for candidate in re.split(r"\s*(?:,|\band\b|&)\s*", raw_names, flags=re.IGNORECASE):
        cleaned = candidate.strip()
        if not cleaned:
            continue
        words = [w for w in cleaned.split() if w.capitalize() not in _AUDIENCE_STOPWORDS]
        if words and words[0][0].isupper():
            name = " ".join(words[:2])
            if name not in names:
                names.append(name)
    return names


def strip_audience_clause(question: str) -> str:
    """Remove 'Audience ...' clauses from question text before subject coverage matching."""
    if not question:
        return ""
    return _AUDIENCE_CLAUSE_RE.sub(" ", question)


