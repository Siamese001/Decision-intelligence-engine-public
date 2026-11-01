"""
3-way semantic merge resolver for JSON registry files.

Resolves non-colliding key insertions and append operations in JSON objects.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple


def _deep_merge_3way(base: Any, ours: Any, theirs: Any) -> Tuple[bool, Any]:
    # If ours and theirs agree, take either
    if ours == theirs:
        return True, ours

    # If theirs didn't change from base, keep ours
    if theirs == base:
        return True, ours

    # If ours didn't change from base, take theirs
    if ours == base:
        return True, theirs

    # Both changed differently
    if isinstance(ours, dict) and isinstance(theirs, dict) and isinstance(base, dict):
        merged_dict: Dict[str, Any] = {}
        all_keys = set(base.keys()).union(ours.keys()).union(theirs.keys())

        for k in all_keys:
            base_val = base.get(k)
            ours_val = ours.get(k)
            theirs_val = theirs.get(k)

            # Key added in ours only
            if k in ours and k not in base and k not in theirs:
                merged_dict[k] = ours_val
            # Key added in theirs only
            elif k in theirs and k not in base and k not in ours:
                merged_dict[k] = theirs_val
            # Key present in both, recurse
            elif k in ours and k in theirs:
                ok, sub_val = _deep_merge_3way(base_val, ours_val, theirs_val)
                if not ok:
                    return False, None
                merged_dict[k] = sub_val
            # Key deleted in one
            elif k in base:
                if k not in ours and k in theirs and theirs_val == base_val:
                    continue  # ours deleted it
                elif k not in theirs and k in ours and ours_val == base_val:
                    continue  # theirs deleted it
                else:
                    return False, None  # conflict on deletion

        return True, merged_dict

    # List concatenation / union for string lists
    if (
        isinstance(ours, list)
        and isinstance(theirs, list)
        and isinstance(base, list)
        and all(isinstance(x, str) for x in ours)
        and all(isinstance(x, str) for x in theirs)
    ):
        base_set = set(base)
        ours_added = [x for x in ours if x not in base_set]
        theirs_added = [x for x in theirs if x not in base_set]
        merged_list = (
            list(base)
            + [x for x in ours_added if x not in base]
            + [x for x in theirs_added if x not in base and x not in ours_added]
        )
        return True, merged_list

    # Primitive conflict
    return False, None


def merge_json_registries(
    ancestor_text: str,
    ours_text: str,
    theirs_text: str,
) -> Tuple[bool, str]:
    try:
        base = json.loads(ancestor_text) if ancestor_text.strip() else {}
        ours = json.loads(ours_text) if ours_text.strip() else {}
        theirs = json.loads(theirs_text) if theirs_text.strip() else {}
    except Exception:
        return False, ""

    ok, merged = _deep_merge_3way(base, ours, theirs)
    if not ok:
        return False, ""

    try:
        formatted = json.dumps(merged, indent=2, sort_keys=True) + "\n"
        return True, formatted
    except Exception:
        return False, ""
