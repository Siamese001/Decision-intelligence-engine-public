#!/usr/bin/env python3
"""
tools/capture_top20_baseline_symbols.py

Extracts and serializes the complete public API surface (classes, functions,
methods, signatures, and constants) for the Top 20 Monolithic Python modules.
Used as the immutable Wave 0 baseline to guarantee zero API regressions during
modularization.
"""

import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOP_20_MODULES = [
    "dual_pipeline.query",
    "dual_pipeline.l2_providers",
    "dual_pipeline.intake",
    "dual_pipeline.release",
    "dual_pipeline.index",
    "dual_pipeline.completion",
    "dual_pipeline.uwg",
    "dual_pipeline.validation_closeout",
    "tools.validate_dual_pipeline_contracts",
    "dual_pipeline.intake_engine",
    "dual_pipeline.evals.runner",
    "dual_pipeline.normalization",
    "dual_pipeline.label_quality",
    "dual_pipeline.ingestion_reasoning",
    "dual_pipeline.facets",
    "dual_pipeline.transcript_stages",
    "dual_pipeline.runtime_evidence",
    "dual_pipeline.card_refactor_release",
    "dual_pipeline.graph_retrieval",
    "dual_pipeline.source_retirement",
]


def inspect_module(mod_name: str) -> Dict[str, Any]:
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:
        return {"error": f"Import failed: {e}", "symbols": {}}

    symbols: Dict[str, Any] = {}

    # Check if module defines __all__
    has_all = hasattr(mod, "__all__")
    all_symbols = mod.__all__ if has_all else None

    for name in dir(mod):
        # Exclude standard dunders unless specifically listed in __all__
        if name.startswith("__") and name.endswith("__"):
            continue

        is_private = name.startswith("_") and (all_symbols is None or name not in all_symbols)

        try:
            val = getattr(mod, name)
        except Exception:
            continue

        sym_type = type(val).__name__
        is_callable = callable(val)
        sig_str = None

        if is_callable:
            try:
                sig_str = str(inspect.signature(val))
            except Exception:
                sig_str = "(...)"

        symbols[name] = {
            "type": sym_type,
            "is_private": is_private,
            "is_callable": is_callable,
            "signature": sig_str,
            "in_all": (name in all_symbols) if all_symbols else False,
        }

    return {
        "module": mod_name,
        "symbol_count": len(symbols),
        "public_count": sum(1 for s in symbols.values() if not s["is_private"]),
        "has_explicit_all": has_all,
        "symbols": symbols,
    }


def main():
    print("Capturing Wave 0 public API baseline for Top 20 modules...")
    inventory = {}
    total_public = 0
    total_symbols = 0

    for mod_name in TOP_20_MODULES:
        print(f"  - Inspecting {mod_name}...")
        mod_data = inspect_module(mod_name)
        inventory[mod_name] = mod_data
        total_symbols += mod_data.get("symbol_count", 0)
        total_public += mod_data.get("public_count", 0)

    out_file = ROOT / "reports" / "baseline_top20_symbols.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Save canonical JSON format
    out_file.write_text(json.dumps(inventory, indent=2, sort_keys=True), encoding="utf-8")

    print("\nBaseline snapshot complete!")
    print(f"Total modules inspected: {len(TOP_20_MODULES)}")
    print(f"Total symbols captured: {total_symbols} ({total_public} public)")
    print(f"Report written to: {out_file}")


if __name__ == "__main__":
    main()
