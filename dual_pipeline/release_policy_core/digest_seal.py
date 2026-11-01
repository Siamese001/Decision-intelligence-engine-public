"""Cryptographic digests, artifact manifests, and decision seal validators for release policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

RETRIEVAL_RELEASE_11_GATES: Tuple[str, ...] = (
    "real_bge_m3",
    "pair_binding",
    "coverage_complete",
    "core_thresholds",
    "delta_thresholds",
    "critical_cases",
    "rank_stability",
    "repeat_stability",
    "forbidden_leakage",
    "latency",
    "bm25_execution",
)


def evaluate_retrieval_11_gates(
    gates_evaluated: Mapping[str, Any],
) -> Tuple[bool, List[str]]:
    """Verify all 11 non-compensating retrieval release gates passed."""
    failed_or_missing: List[str] = []
    for gate_name in RETRIEVAL_RELEASE_11_GATES:
        if gate_name not in gates_evaluated:
            failed_or_missing.append(f"{gate_name} (MISSING)")
        elif not bool(gates_evaluated[gate_name]):
            failed_or_missing.append(f"{gate_name} (FAILED)")
    return len(failed_or_missing) == 0, failed_or_missing


def compute_release_evaluation_digest(evaluation_data: Mapping[str, Any]) -> str:
    """Computes a deterministic SHA-256 digest of release evaluation data."""
    clean = {
        k: v
        for k, v in evaluation_data.items()
        if k not in ("evaluation_receipt_digest", "judge_failure_diagnostics", "retry_scope")
    }
    canonical_json = json.dumps(clean, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_decision_seal(manifest: Mapping[str, Any]) -> str:
    """Computes a deterministic RFC-8259 SHA-256 seal of an artifact manifest excluding existing seal."""
    clean = {k: v for k, v in manifest.items() if k != "decision_seal_sha256"}
    canonical_json = json.dumps(clean, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def build_artifact_manifest(
    f1_text: str,
    f2_text: str,
    f3_text: str,
    model: str,
    reasoning_effort: str,
    judge_eval_digest: str = "",
) -> Dict[str, Any]:
    """Builds a tamper-proof cryptographic artifact manifest for release decisions."""
    manifest = {
        "output_1_sha256": hashlib.sha256(f1_text.encode("utf-8")).hexdigest(),
        "output_2_sha256": hashlib.sha256(f2_text.encode("utf-8")).hexdigest(),
        "output_3_sha256": hashlib.sha256(f3_text.encode("utf-8")).hexdigest(),
        "evaluator_model": model,
        "reasoning_effort": reasoning_effort,
        "judge_evaluation_digest": judge_eval_digest,
    }
    manifest["decision_seal_sha256"] = compute_decision_seal(manifest)
    return manifest


def verify_release_evaluation_digest(judge_report: Mapping[str, Any]) -> bool:
    """Verifies that the evaluation_receipt_digest matches the deterministic SHA-256 hash of the evaluation content."""
    receipt_digest = judge_report.get("evaluation_receipt_digest")
    if not receipt_digest or not isinstance(receipt_digest, str):
        return False
    data_without_digest = {
        k: v
        for k, v in judge_report.items()
        if k not in ("evaluation_receipt_digest", "judge_failure_diagnostics", "retry_scope")
    }
    expected = compute_release_evaluation_digest(data_without_digest)
    return receipt_digest == expected


def verify_decision_seal(
    decision_data: Mapping[str, Any],
    f1_text: Optional[str] = None,
    f2_text: Optional[str] = None,
    f3_text: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """Verifies that decision data has not been tampered with and matches active artifact content."""
    errors: List[str] = []
    manifest = decision_data.get("artifact_manifest")
    if not isinstance(manifest, dict):
        return False, ["Missing artifact_manifest in decision data"]

    expected_seal = compute_decision_seal(manifest)
    actual_seal = manifest.get("decision_seal_sha256")
    if not actual_seal or actual_seal != expected_seal:
        errors.append(f"Decision seal mismatch: expected {expected_seal}, got {actual_seal}")

    for idx, txt in enumerate((f1_text, f2_text, f3_text), 1):
        h = hashlib.sha256((txt or "").encode("utf-8")).hexdigest()
        key = f"output_{idx}_sha256"
        if manifest.get(key) != h:
            errors.append(f"Output {idx} content drifted: manifest={manifest.get(key)}, active={h}")

    return len(errors) == 0, errors


def _read_json_safe(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if len(raw) <= 10 or raw in ("{}", "null", "[]"):
            return None
        return json.loads(raw)
    except Exception:
        return None


def _is_evidence_envelope_package(package_path: Path) -> bool:
    pkg_data = _read_json_safe(package_path)
    if not isinstance(pkg_data, dict):
        return False
    contract_id = str(pkg_data.get("contract_id", ""))
    return (
        contract_id.endswith(":evidence-envelope-intake:1.0.0")
        or contract_id.endswith(":google-meet-gemini-intake:1.0.0")
    )


def _read_first_glob(parent_dir: Path, pattern: str) -> str:
    matches = list(parent_dir.glob(pattern))
    return matches[0].read_text(encoding="utf-8") if matches else ""


def _match_glob_by_sha256(parent_dir: Path, pattern: str, target_hash: Optional[str]) -> str:
    if not target_hash:
        return ""
    for candidate in parent_dir.glob(pattern):
        if candidate.is_file():
            raw = candidate.read_text(encoding="utf-8")
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() == target_hash:
                return raw
    return ""


def _resolve_f1_fallback_from_metadata(parent_dir: Path) -> str:
    meta_p = parent_dir / "metadata.json"
    if not meta_p.is_file():
        return ""
    meta = _read_json_safe(meta_p) or {}
    f1_text = meta.get("source_text") or meta.get("forensic_transcript") or ""
    if f1_text:
        return str(f1_text)
    for key in ("input_path", "_document_source_path"):
        if meta.get(key):
            cand = Path(meta[key])
            if cand.is_file():
                return cand.read_text(encoding="utf-8")
    return ""


def _resolve_f1_source_text(parent_dir: Path, decision: Dict[str, Any], target_f1_hash: Optional[str]) -> str:
    for pattern in ("*source*.txt", "*source_document*.txt"):
        matched = _match_glob_by_sha256(parent_dir, pattern, target_f1_hash)
        if matched:
            return matched
    if decision.get("mode") == "document":
        src_doc = parent_dir / "source_document.txt"
        if src_doc.is_file():
            return src_doc.read_text(encoding="utf-8")
        doc_glob = _read_first_glob(parent_dir, "*source_document*.txt")
        if doc_glob:
            return doc_glob
    src_glob = _read_first_glob(parent_dir, "*source*.txt")
    if src_glob:
        return src_glob
    return _resolve_f1_fallback_from_metadata(parent_dir)


def _resolve_release_artifacts(parent_dir: Path, decision: Dict[str, Any]) -> Tuple[str, str, str]:
    manifest = decision.get("artifact_manifest") if isinstance(decision, dict) else {}
    target_f3_hash = manifest.get("output_3_sha256") if isinstance(manifest, dict) else None
    f3_text = _match_glob_by_sha256(parent_dir, "03_*canonical_signal_projection.json", target_f3_hash)
    if not f3_text:
        f3_text = _read_first_glob(parent_dir, "03_*canonical_signal_projection.json")
    f1_text = _read_first_glob(parent_dir, "01_*hitl_resolved_transcript.md")
    f2_text = _read_first_glob(parent_dir, "02_*executive_recap.md")
    if not f1_text:
        target_f1_hash = manifest.get("output_1_sha256") if isinstance(manifest, dict) else None
        f1_text = _resolve_f1_source_text(parent_dir, decision, target_f1_hash)
    return f1_text, f2_text, f3_text


def _collect_referenced_attachments_text(parent_dir: Path) -> str:
    ref_dir = parent_dir / "referenced_artifacts"
    if not ref_dir.is_dir():
        return ""
    parts: list[str] = []
    for p in sorted(ref_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in (".md", ".txt", ".json", ".csv"):
            try:
                parts.append(f"{p.name}\n{p.read_text(encoding='utf-8')}")
            except Exception:
                pass
    return "\n\n".join(parts)
