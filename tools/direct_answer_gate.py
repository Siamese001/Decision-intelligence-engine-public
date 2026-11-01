"""Visible response checks for time-sensitive Copilot and NEE query turns."""
from __future__ import annotations

import re
from typing import Any


def validate_direct_answer(response: str, route: str, word_limit: int | None = None) -> dict[str, Any]:
    issues: list[str] = []
    text = response.strip()
    for marker in ("Agent Reasoning Scratchpad", "Active Model & Provider", "API & Tool Invocations"):
        if marker.lower() in text.lower():
            issues.append(f"Direct answer contains the forbidden preamble: {marker}.")
    if route == "query":
        counted = re.split(r"\n#{2,3}\s+Evidence\s+&\s+Graph\s+Lineage\b", text, flags=re.IGNORECASE)[0].strip()
    else:
        counted = text.removeprefix("```text\n").removesuffix("\n```")
    if word_limit is not None and len(counted.split()) > word_limit:
        issues.append(f"Visible answer must contain at most {word_limit} words.")
    if route == "copilot":
        if not (text.startswith("```text\n") and text.endswith("\n```") and text.count("```") == 2):
            issues.append("Copilot must contain exactly one text fence and no surrounding prose.")
    elif route == "query":
        if text.startswith("```"):
            issues.append("Query answers must use direct prose, not a Copilot fence.")
    else:
        issues.append(f"Unsupported direct-answer route: {route}.")
    return {"status": "PASS" if not issues else "FAIL", "issues": issues}
