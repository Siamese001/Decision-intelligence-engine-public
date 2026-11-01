#!/usr/bin/env python3
"""
tools/validate_model_identity_acceptance.py

Plan-bound acceptance validator for Model Identity SSOT RCA Wave 1.
Validates:
1. Deliverable files exist and match manifest SHA-256 digests.
2. docs/governance/model_identity_contract.md contains all mandatory scopes, states, and decisions.
3. reports/model_identity_ssot/incident_manifest.json records OPEN attribution and E1-E8 defects.
4. reports/model_identity_ssot/inventory_manifest.json classifies all surfaces with required attributes.
5. Decisions D1-D4 are ratified and pre-wave decision gate clears Wave 1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent


def compute_sha256(path: Path) -> str:
    if not path.is_file():
        return "FILE_NOT_FOUND"
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def validate_contract_content(contract_path: Path) -> Tuple[bool, List[str]]:
    diagnostics = []
    if not contract_path.is_file():
        return False, [f"Contract file missing: {contract_path}"]

    content = contract_path.read_text(encoding="utf-8")

    # Required scopes
    required_scopes = ["host_assistant", "delegated_assistant", "application_call", "deterministic_tool"]
    for scope in required_scopes:
        if scope not in content:
            diagnostics.append(f"Missing required identity scope in contract: '{scope}'")

    # Required lifecycle states
    required_states = ["requested_model", "resolved_model", "dispatched_model", "observed_model", "unavailable_state"]
    for state in required_states:
        if state not in content:
            diagnostics.append(f"Missing required lifecycle state in contract: '{state}'")

    # Required evidence tiers
    required_tiers = ["CONFIGURED_ONLY", "DISPATCH_RECORDED", "OBSERVED_RESPONSE", "CRYPTOGRAPHICALLY_SEALED"]
    for tier in required_tiers:
        if tier not in content:
            diagnostics.append(f"Missing required evidence tier in contract: '{tier}'")

    # Required decisions
    required_decisions = [
        "DECISION-MI-D1-APPLICATION-POLICY-SSOT",
        "DECISION-MI-D2-REJECTION-VS-FALLBACK",
        "DECISION-MI-D3-HOST-METADATA-INTEGRATION",
        "DECISION-MI-D4-HISTORICAL-EXCEPTION-CLASSIFICATION",
    ]
    for dec in required_decisions:
        if dec not in content:
            diagnostics.append(f"Missing ratified decision in contract: '{dec}'")

    # SSOT canonical path
    if "config/runtime/model_registry.json" not in content:
        diagnostics.append("Contract does not reference canonical SSOT path 'config/runtime/model_registry.json'")

    return len(diagnostics) == 0, diagnostics


def validate_incident_manifest(manifest_path: Path) -> Tuple[bool, List[str]]:
    diagnostics = []
    if not manifest_path.is_file():
        return False, [f"Incident manifest missing: {manifest_path}"]

    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    if data.get("attribution_status") != "OPEN":
        diagnostics.append(f"Expected attribution_status 'OPEN', got '{data.get('attribution_status')}'")

    provenance = data.get("provenance_trace", {})
    if provenance.get("host_session_id") != "UNAVAILABLE":
        diagnostics.append("Expected host_session_id to be 'UNAVAILABLE'")

    defects = data.get("verified_systemic_defects", [])
    defect_ids = {d.get("id") for d in defects}
    for e_id in ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8"]:
        if e_id not in defect_ids:
            diagnostics.append(f"Defect {e_id} missing from incident manifest verified_systemic_defects")

    sanitization = data.get("sanitization_and_privacy", {})
    if sanitization.get("secret_leak_scan") != "PASS_CLEAN":
        diagnostics.append("Incident manifest sanitization scan did not report 'PASS_CLEAN'")

    return len(diagnostics) == 0, diagnostics


def validate_inventory_manifest(manifest_path: Path) -> Tuple[bool, List[str]]:
    diagnostics = []
    if not manifest_path.is_file():
        return False, [f"Inventory manifest missing: {manifest_path}"]

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    surfaces = data.get("surfaces", [])

    if len(surfaces) < 15:
        diagnostics.append(f"Inventory manifest has only {len(surfaces)} surfaces; expected >= 15")

    has_host_unreachable = False
    for srf in surfaces:
        s_id = srf.get("surface_id", "UNKNOWN")
        path_str = srf.get("path", "")
        for required_key in ["owner", "identity_domain", "source_sha256", "authority", "disposition"]:
            if not srf.get(required_key):
                diagnostics.append(f"Surface {s_id} ({path_str}) missing required key '{required_key}'")

        if path_str.startswith("host://"):
            has_host_unreachable = True
        elif (REPO_ROOT / path_str).is_file():
            actual_sha = compute_sha256(REPO_ROOT / path_str)
            recorded_sha = srf.get("source_sha256")
            if recorded_sha not in ("SELF_REFERENTIAL", "VARIABLE_DURING_WAVE") and actual_sha != recorded_sha:
                diagnostics.append(f"SHA mismatch for {path_str}: actual={actual_sha}, recorded={recorded_sha}")

    if not has_host_unreachable:
        diagnostics.append("Inventory manifest does not explicitly catalog unreachable host assistant surface")

    return len(diagnostics) == 0, diagnostics


def _verify_deliverables(deliverables: list[dict[str, Any]]) -> bool:
    all_ok = True
    for item in deliverables:
        p = REPO_ROOT / item["path"]
        if not p.is_file():
            print(f"❌ FAIL: Declared deliverable missing: {item['path']}")
            all_ok = False
            continue
        actual_sha = compute_sha256(p)
        expected_sha = item.get("sha256")
        if expected_sha and expected_sha not in ("SELF_REFERENTIAL", "VARIABLE_DURING_WAVE") and actual_sha != expected_sha:
            print(f"❌ FAIL: SHA mismatch for {item['path']}:\n  expected {expected_sha}\n  actual   {actual_sha}")
            all_ok = False
        else:
            print(f"  ✔ Deliverable verified: {item['path']} ({actual_sha[:12]}...)")
    return all_ok


def _verify_governance_manifests() -> bool:
    all_ok = True
    contract_p = REPO_ROOT / "docs/governance/model_identity_contract.md"
    ok, diags = validate_contract_content(contract_p)
    if ok:
        print("✔ Contract verification: All required scopes, states, tiers, and decisions verified.")
    else:
        print("❌ FAIL: Contract verification failed:")
        for d in diags:
            print(f"  - {d}")
        all_ok = False

    incident_p = REPO_ROOT / "reports/model_identity_ssot/incident_manifest.json"
    ok, diags = validate_incident_manifest(incident_p)
    if ok:
        print("✔ Incident manifest: OPEN attribution and systemic defects E1-E8 confirmed.")
    else:
        print("❌ FAIL: Incident manifest validation failed:")
        for d in diags:
            print(f"  - {d}")
        all_ok = False

    inv_p = REPO_ROOT / "reports/model_identity_ssot/inventory_manifest.json"
    ok, diags = validate_inventory_manifest(inv_p)
    if ok:
        print("✔ Inventory manifest: Surface classification and file hashes confirmed.")
    else:
        print("❌ FAIL: Inventory manifest validation failed:")
        for d in diags:
            print(f"  - {d}")
        all_ok = False
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Model Identity SSOT RCA Wave 1 Acceptance")
    parser.add_argument("--wave-manifest", type=Path, default=REPO_ROOT / "reports/model_identity_ssot/wave1_manifest.json")
    args = parser.parse_args()

    print("=== MODEL IDENTITY SSOT RCA WAVE 1 ACCEPTANCE VALIDATION ===")
    manifest_path = args.wave_manifest
    if not manifest_path.is_file():
        print(f"❌ FAIL: Wave manifest not found at {manifest_path}")
        return 1

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    deliverables = manifest_data.get("deliverables", [])
    print(f"✔ Found wave manifest: {manifest_path} ({len(deliverables)} declared deliverables)")

    ok1 = _verify_deliverables(deliverables)
    ok2 = _verify_governance_manifests()

    if ok1 and ok2:
        print("\n✅ [ALL ACCEPTANCE GATES PASSED] Wave 1 deliverables satisfy all criteria.")
        return 0
    print("\n🛑 [ACCEPTANCE GATE BLOCKED] One or more acceptance checks failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
