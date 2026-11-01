"""Pipeline B (Extraction): Resilient JSON Parser & Repair Utility.

Handles typical LLM completion artifacts:
1. Markdown code fences (```json ... ```).
2. Leading/trailing conversational commentary.
3. Trailing commas before closing braces/brackets.
4. Truncated completions caused by max_tokens boundaries (unclosed strings, braces, brackets).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Union

logger = logging.getLogger("pipeline_b_extraction.json_repair")


def strip_markdown_fences(text: str) -> str:
    """Strips markdown code blocks (e.g. ```json ... ```) from LLM output."""
    cleaned = text.strip()
    # Match ```json ... ``` or ``` ... ```
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, cleaned, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return cleaned


def extract_outer_json_structure(text: str) -> str:
    """Finds substring spanning from the first '{' or '[' to the last '}' or ']'."""
    first_brace = text.find("{")
    first_bracket = text.find("[")

    if first_brace == -1 and first_bracket == -1:
        return text

    if first_brace != -1 and first_bracket != -1:
        start_idx = min(first_brace, first_bracket)
    elif first_brace != -1:
        start_idx = first_brace
    else:
        start_idx = first_bracket

    last_brace = text.rfind("}")
    last_bracket = text.rfind("]")
    end_idx = max(last_brace, last_bracket)

    if end_idx != -1 and end_idx >= start_idx:
        return text[start_idx : end_idx + 1]
    return text[start_idx:]


def _update_stack(ch: str, stack: List[str]) -> None:
    if ch in ("{", "["):
        stack.append(ch)
    elif ch == "}" and stack and stack[-1] == "{" or ch == "]" and stack and stack[-1] == "[":
        stack.pop()


def _scan_delimiters(s: str) -> Tuple[bool, List[str]]:
    in_string = False
    escape = False
    stack: List[str] = []

    for ch in s:
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        else:
            _update_stack(ch, stack)

    return in_string, stack


def _close_delimiters(s: str, stack: List[str]) -> str:
    while stack:
        delim = stack.pop()
        s = re.sub(r",\s*$", "", s)
        s += "}" if delim == "{" else "]"

    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    return s


def repair_truncated_json(text: str) -> str:
    """Attempts heuristic repairs on truncated JSON text."""
    s = text.strip()
    if not s:
        return "{}"

    s = re.sub(r",\s*$", "", s)
    in_string, stack = _scan_delimiters(s)

    if in_string:
        s += '"'

    s = re.sub(r",\s*$", "", s)
    s = re.sub(r":\s*tru\s*$", ": true", s)
    s = re.sub(r":\s*fal(?:s)?\s*$", ": false", s)
    s = re.sub(r":\s*nul?\s*$", ": null", s)
    s = re.sub(r":\s*$", ': ""', s)

    return _close_delimiters(s, stack)


def repair_and_parse_json(raw_text: str) -> Union[Dict[str, Any], List[Any]]:
    """
    Robust multi-stage JSON parser:
    1. Direct json.loads.
    2. Strip markdown fences and retry.
    3. Extract outermost JSON structure, strip trailing commas, and retry.
    4. Apply truncation repair (unclosed strings, braces, brackets) and retry.
    5. Raises ValueError / JSONDecodeError if all recovery strategies fail.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Empty or whitespace-only JSON input cannot be parsed.")

    # Stage 1: Fast path direct parse
    try:
        return json.loads(raw_text)
    except Exception:
        pass

    # Stage 2: Strip markdown fences
    fenced = strip_markdown_fences(raw_text)
    try:
        return json.loads(fenced)
    except Exception:
        pass

    # Stage 3: Extract outer JSON structure and remove trailing commas
    extracted = extract_outer_json_structure(fenced)
    no_trailing_commas = re.sub(r",\s*([\]}])", r"\1", extracted)
    try:
        return json.loads(no_trailing_commas)
    except Exception:
        pass

    # Stage 4: Repair truncated JSON
    repaired = repair_truncated_json(no_trailing_commas)
    try:
        return json.loads(repaired)
    except Exception as e:
        logger.warning(f"Failed to repair malformed JSON: {e} | Raw snippet: {raw_text[:200]}")
        raise ValueError(f"Unable to parse or repair JSON: {e}") from e
