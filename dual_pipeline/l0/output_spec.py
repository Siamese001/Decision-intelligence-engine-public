"""Dual-Pipeline Layer 0: Deliverable output specifications and canonical parsing.

Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Optional, Set

CANONICAL_OUTPUT_MAP: Dict[str, str] = {
    "1": "output_1",
    "01": "output_1",
    "output_1": "output_1",
    "output1": "output_1",
    "2": "output_2",
    "02": "output_2",
    "output_2": "output_2",
    "output2": "output_2",
    "3": "output_3",
    "03": "output_3",
    "output_3": "output_3",
    "output3": "output_3",
}

ALL_OUTPUTS: FrozenSet[str] = frozenset({"output_1", "output_2", "output_3"})


def parse_regenerate_spec(spec: Optional[str]) -> Set[str]:
    """
    Parses and canonicalizes the --regenerate CLI argument.

    Accepts:
      - None or "all" -> returns {"output_1", "output_2", "output_3"}
      - "02,03" -> returns {"output_2", "output_3"}
      - "01" -> returns {"output_1"}
      - "2,3" -> returns {"output_2", "output_3"}

    Raises:
      ValueError if an invalid deliverable spec is provided (e.g. '04', 'invalid').
    """
    if not spec or str(spec).strip().lower() in ("all", "*"):
        return set(ALL_OUTPUTS)

    raw_tokens = [tok.strip().lower() for tok in str(spec).split(",") if tok.strip()]
    if not raw_tokens:
        return set(ALL_OUTPUTS)

    result: Set[str] = set()
    for tok in raw_tokens:
        if tok == "all":
            return set(ALL_OUTPUTS)
        if tok not in CANONICAL_OUTPUT_MAP:
            raise ValueError(
                f"Invalid --regenerate deliverable '{tok}'. "
                f"Allowed values are comma-separated numbers: '01', '02', '03', or 'all' "
                f"(received: '{spec}')."
            )
        result.add(CANONICAL_OUTPUT_MAP[tok])

    return result
