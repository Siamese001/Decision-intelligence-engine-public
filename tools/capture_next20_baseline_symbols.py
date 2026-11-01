#!/usr/bin/env python3
"""
tools/capture_next20_baseline_symbols.py

Extracts and serializes the complete public API surface (classes, functions,
methods, signatures, and constants) for the Next 20 Monolithic Python modules
(Waves 7 through 11).
Used as the immutable baseline to guarantee zero API regressions during modularization.
"""

import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Preload release to avoid circular import during baseline capture
import dual_pipeline.release  # noqa: F401

NEXT_20_MODULES = [
    # Wave 7: Graph Mutation Integrity & Remediation Gateway
    "dual_pipeline.graph_writer",
    "dual_pipeline.graph_delta",
    "dual_pipeline.candidate_cleanup",
    "dual_pipeline.combined_remediation",
    # Wave 8: LLM Judge Governance, Fail-Closed QA & Model Binding
    "dual_pipeline.llm_judge",
    "dual_pipeline.transcript_qa",
    "dual_pipeline.transcript_pipeline",
    "dual_pipeline.speaker_disambiguation",
    # Wave 9: Canonical Identity, Entity Resolution & Evidence Spans
    "dual_pipeline.identity",
    "dual_pipeline.identity_resolver",
    "dual_pipeline.source_spans",
    "dual_pipeline.a2a_finalization",
    # Wave 10: Deterministic Query Routing, L1 Shadow & Provider Policy
    "dual_pipeline.query_router",
    "dual_pipeline.query_l1_shadow",
    "dual_pipeline.query_l1_actions",
    "dual_pipeline.l2_policy",
    # Wave 11: Retrieval Regression Gates, Semantic Cache & Preflight Assurance
    "dual_pipeline.retrieval_release",
    "dual_pipeline.retrieval_suite",
    "dual_pipeline.semantic_cache",
    "dual_pipeline.preflight",
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
    print("Capturing Next 20 public API baseline (Waves 7-11)...")
    inventory = {}
    total_public = 0
    total_symbols = 0

    for mod_name in NEXT_20_MODULES:
        print(f"  - Inspecting {mod_name}...")
        mod_data = inspect_module(mod_name)
        inventory[mod_name] = mod_data
        total_symbols += mod_data.get("symbol_count", 0)
        total_public += mod_data.get("public_count", 0)

    out_file = ROOT / "reports" / "baseline_next20_symbols.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Save canonical JSON format
    out_file.write_text(json.dumps(inventory, indent=2, sort_keys=True), encoding="utf-8")

    print("\nBaseline snapshot complete!")
    print(f"Total modules inspected: {len(NEXT_20_MODULES)}")
    print(f"Total symbols captured: {total_symbols} ({total_public} public)")
    print(f"Report written to: {out_file}")


if __name__ == "__main__":
    main()
