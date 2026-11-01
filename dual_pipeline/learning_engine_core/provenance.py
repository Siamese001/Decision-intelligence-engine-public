"""
dual_pipeline/learning_engine_core/provenance.py

Cryptographic provenance binding and verification for learning change proposals.
Binds source failure events, run IDs, replay definitions, and content digests.
Provides source digest inspection resolving verified, unavailable, and tampered sources.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from dual_pipeline.contract_validation import canonical_json_bytes

PROVENANCE_EXTENSION_KEY = "com.partner_advisory.nee.provenance"


def build_signed_provenance(
    proposal_id: str,
    evidence_event_ids: Sequence[str],
    run_ids: Sequence[str],
    proposed_change: str,
    replay_definition: Mapping[str, Any],
    source_receipt_digests: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Constructs a deterministic cryptographic provenance payload."""
    payload = {
        "proposal_id": proposal_id,
        "evidence_event_ids": sorted(list(evidence_event_ids)),
        "run_ids": sorted(list(run_ids)),
        "proposed_change_hash": hashlib.sha256(
            proposed_change.encode("utf-8")
        ).hexdigest(),
        "replay_definition": dict(replay_definition),
        "source_receipt_digests": sorted(list(source_receipt_digests or [])),
    }
    provenance_digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return {
        **payload,
        "provenance_digest": provenance_digest,
        "verified": True,
    }


def verify_proposal_provenance(proposal: Mapping[str, Any]) -> Tuple[bool, str]:
    """Validates the cryptographic provenance binding of a proposal."""
    ext = proposal.get("extensions", {})
    if not isinstance(ext, dict):
        return False, "Proposal extensions is not a dictionary."

    prov = ext.get(PROVENANCE_EXTENSION_KEY)
    if not isinstance(prov, dict):
        return False, f"Missing required provenance extension '{PROVENANCE_EXTENSION_KEY}'."

    recorded_digest = prov.get("provenance_digest")
    if not recorded_digest or not isinstance(recorded_digest, str):
        return False, "Missing or invalid provenance_digest in proposal."

    payload = {
        "proposal_id": prov.get("proposal_id"),
        "evidence_event_ids": sorted(list(prov.get("evidence_event_ids") or [])),
        "run_ids": sorted(list(prov.get("run_ids") or [])),
        "proposed_change_hash": prov.get("proposed_change_hash"),
        "replay_definition": dict(prov.get("replay_definition") or {}),
        "source_receipt_digests": sorted(
            list(prov.get("source_receipt_digests") or [])
        ),
    }
    recalculated = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    if recorded_digest != recalculated:
        return False, f"Provenance digest mismatch: expected {recalculated}, got {recorded_digest}."

    if sorted(proposal.get("evidence_event_ids", [])) != sorted(prov.get("evidence_event_ids", [])):
        return False, "Evidence event IDs in provenance do not match proposal evidence_event_ids."

    if sorted(proposal.get("run_ids", [])) != sorted(prov.get("run_ids", [])):
        return False, "Run IDs in provenance do not match proposal run_ids."

    prop_change = proposal.get("proposed_change", "")
    actual_change_hash = hashlib.sha256(prop_change.encode("utf-8")).hexdigest()
    if actual_change_hash != prov.get("proposed_change_hash"):
        return False, "Proposed change text in proposal does not match provenance hash."

    return True, "Provenance binding verified successfully."


def _match_digest_in_dir(directory: Path, target_hash: str) -> Optional[Path]:
    """Scans JSON files in a directory to find a matching content SHA-256 hash."""
    for f in directory.glob("*.json"):
        try:
            if hashlib.sha256(f.read_bytes()).hexdigest() == target_hash:
                return f
        except Exception:
            continue
    return None


def _find_candidate_file(root: Path, identifier: str) -> Optional[Path]:
    """Finds a candidate file matching identifier by name or content hash in receipt directories."""
    clean_id = identifier.replace("MISSING_SOURCE:", "").strip()
    target_dirs = [root / "evals", root / "ledger", root / "reports", root]
    for td in target_dirs:
        if not td.is_dir():
            continue
        exact = td / f"{clean_id}.json"
        if exact.is_file():
            return exact
        if len(clean_id) == 64:
            matched = _match_digest_in_dir(td, clean_id)
            if matched is not None:
                return matched
    return None


def _find_target_file(
    item: str,
    explicit_files: Mapping[str, Path],
    root: Optional[Path],
    rid: Optional[str] = None,
) -> Optional[Path]:
    """Resolves an explicit file mapping or candidate path for a digest/identifier."""
    for key, p in explicit_files.items():
        if key in (item, rid) or p.stem in (item, rid) or (rid and rid in key):
            return p
    if root is not None:
        if rid:
            found = _find_candidate_file(root, rid)
            if found:
                return found
        return _find_candidate_file(root, item)
    return None


def _inspect_single_source(
    item: str,
    target_file: Optional[Path],
    rid: Optional[str] = None,
) -> Tuple[str, str]:
    """Inspects a single source item and returns (source_name, status)."""
    if item.startswith("MISSING_SOURCE:"):
        return item.split(":", 1)[1], "UNAVAILABLE"
    display_name = target_file.name if target_file is not None else (f"{rid}.json" if rid else item[:16])
    if target_file is None or not target_file.is_file():
        return display_name, "UNAVAILABLE"
    try:
        actual_digest = hashlib.sha256(target_file.read_bytes()).hexdigest()
        status = "VERIFIED" if actual_digest == item else "TAMPERED"
        return display_name, status
    except Exception:
        return display_name, "UNAVAILABLE"


def inspect_proposal_sources(
    proposal: Mapping[str, Any],
    runtime_root: Optional[Path] = None,
    source_receipt_files: Optional[Mapping[str, Path]] = None,
) -> Dict[str, Any]:
    """Inspects and resolves each source digest referenced in a proposal."""
    p_id = proposal.get("proposal_id", "unknown")
    ext = proposal.get("extensions", {})
    prov = ext.get(PROVENANCE_EXTENSION_KEY, {})
    learning_ext = ext.get("com.partner_advisory.nee.learning", {})

    recorded_digests: List[str] = (
        prov.get("source_receipt_digests")
        or learning_ext.get("source_receipt_digests")
        or []
    )
    receipt_ids: List[str] = learning_ext.get("receipt_ids") or []
    paired: List[Tuple[Optional[str], str]] = (
        list(zip(receipt_ids, recorded_digests))
        if len(receipt_ids) == len(recorded_digests)
        else [(None, d) for d in recorded_digests]
    )

    root = Path(runtime_root) if runtime_root else None
    explicit_files = dict(source_receipt_files or {})

    sources_report: Dict[str, str] = {}
    verified_count = 0
    unavailable_count = 0
    tampered_count = 0

    for rid, item in paired:
        target = _find_target_file(item, explicit_files, root, rid=rid)
        name, status = _inspect_single_source(item, target, rid=rid)
        sources_report[name] = status
        if status == "VERIFIED":
            verified_count += 1
        elif status == "TAMPERED":
            tampered_count += 1
        else:
            unavailable_count += 1

    all_verified = (tampered_count == 0 and unavailable_count == 0 and verified_count > 0)
    return {
        "proposal_id": p_id,
        "verified": all_verified,
        "sources": sources_report,
        "verified_count": verified_count,
        "unavailable_count": unavailable_count,
        "tampered_count": tampered_count,
        "has_unavailable": unavailable_count > 0,
        "has_tampered": tampered_count > 0,
    }
