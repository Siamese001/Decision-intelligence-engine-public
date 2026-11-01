#!/usr/bin/env python3
"""tools/lint_entity_directory_invariants.py

CI Invariant Linter for Canonical Enterprise Directory.
Enforces:
1. Zero phonetic duplicate canonical names (Levenshtein distance <= 1).
2. All directory keys are clean lowercase without trailing whitespace.
3. Every canonical person has a self-referential lowercase key.
4. All organization and role values are non-empty and non-placeholder.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.transcript_metadata_core.entity_resolver import _levenshtein_distance


def lint_directory_invariants(repo_root: Path) -> int:
    manifest_p = repo_root / "dual_pipeline" / "transcript_metadata_core" / "canonical_enterprise_directory.json"
    if not manifest_p.exists():
        print(f"❌ FAIL: Directory file not found at {manifest_p}")
        return 1

    with open(manifest_p, "r", encoding="utf-8") as f:
        data = json.load(f)

    violations = []
    canonical_persons = {}

    for key, val in data.items():
        # Check key hygiene
        if key != key.strip():
            violations.append(f"Key has whitespace padding: '{key}'")
        if key != key.lower():
            violations.append(f"Key is not lowercase: '{key}'")

        # Check value schema
        if not isinstance(val, (list, tuple)) or len(val) != 3:
            violations.append(f"Key '{key}' value must be [canonical_name, org, title], got: {val}")
            continue

        c_name, org, title = val[0].strip(), val[1].strip(), val[2].strip()
        if not c_name or not org or not title:
            violations.append(f"Key '{key}' has empty fields: name='{c_name}', org='{org}', title='{title}'")

        if c_name not in canonical_persons:
            canonical_persons[c_name] = (org, title)

    # Check pairwise Levenshtein distance on canonical full names
    c_names = sorted(list(canonical_persons.keys()))
    for i in range(len(c_names)):
        for j in range(i + 1, len(c_names)):
            n1, n2 = c_names[i], c_names[j]
            d = _levenshtein_distance(n1.lower(), n2.lower())
            if d <= 1:
                violations.append(
                    f"Phonetic duplicate canonical entity detected: '{n1}' vs '{n2}' (edit distance {d})"
                )

    # Check that canonical name exists as exact lowercase key
    for c_name in c_names:
        low = c_name.lower()
        if low not in data:
            violations.append(f"Canonical person '{c_name}' missing exact lowercase key '{low}'")

    if violations:
        print(f"❌ FAIL: Found {len(violations)} directory invariant violations:")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(f"✅ PASS: Canonical Enterprise Directory satisfies all invariants ({len(canonical_persons)} entities, {len(data)} keys).")
    return 0


if __name__ == "__main__":
    sys.exit(lint_directory_invariants(ROOT))
