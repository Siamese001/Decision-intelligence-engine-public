#!/usr/bin/env python3
"""
tools/verify_symbol_compatibility.py

Verifies that a refactored module or package satisfies 100% backward
compatibility against reports/baseline_top20_symbols.json.
"""

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE_FILE_TOP20 = ROOT / "reports" / "baseline_top20_symbols.json"
BASELINE_FILE_NEXT20 = ROOT / "reports" / "baseline_next20_symbols.json"


def verify_module(mod_name: str) -> bool:
    baseline = {}
    if BASELINE_FILE_TOP20.exists():
        baseline.update(json.load(BASELINE_FILE_TOP20.open("r", encoding="utf-8")))
    if BASELINE_FILE_NEXT20.exists():
        baseline.update(json.load(BASELINE_FILE_NEXT20.open("r", encoding="utf-8")))

    if not baseline:
        print("Error: No baseline files found.")
        return False

    if mod_name not in baseline:
        print(f"Error: Module {mod_name} not found in baseline files.")
        return False

    expected_mod = baseline[mod_name]
    expected_symbols = expected_mod.get("symbols", {})

    try:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        mod = importlib.import_module(mod_name)
    except Exception as e:
        print(f"FAIL: Failed to import {mod_name}: {e}")
        return False

    actual_names = set(dir(mod))
    missing = []
    mismatched = []

    for name, expected_info in expected_symbols.items():
        if name not in actual_names:
            missing.append(name)
            continue

        try:
            val = getattr(mod, name)
            if expected_info.get("is_callable") and not callable(val):
                mismatched.append(f"{name}: expected callable, got {type(val)}")
        except Exception as e:
            missing.append(f"{name} (getattr error: {e})")

    if missing or mismatched:
        print(f"FAIL: {mod_name} has symbol regressions:")
        if missing:
            print(f"  Missing symbols ({len(missing)}): {missing[:15]}")
        if mismatched:
            print(f"  Mismatched symbols ({len(mismatched)}): {mismatched[:15]}")
        return False

    print(f"PASS: {mod_name} preserves 100% of baseline symbols ({len(expected_symbols)} checked).")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/verify_symbol_compatibility.py <module_name>")
        sys.exit(1)

    target = sys.argv[1]
    success = verify_module(target)
    sys.exit(0 if success else 1)
