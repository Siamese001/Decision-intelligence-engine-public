"""
dual_pipeline/cli/promote.py

CLI handler for 'nee backtest-promote'.
Promotes a verified backtest meeting output directory to the active knowledge graph via the Universal Write Gateway without calling LLM providers ($0 re-inference cost).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.core import ROOT
from dual_pipeline.graph_preview_core.preview import generate_graph_delta_preview

logger = logging.getLogger(__name__)


def _resolve_target_output_03(meeting_dir: Path) -> Path:
    out3_candidates = list(meeting_dir.glob("03_*_canonical_signal_projection.json"))
    if not out3_candidates:
        raise FileNotFoundError(
            f"No 03_*_canonical_signal_projection.json found in {meeting_dir}"
        )
    out3_file = out3_candidates[0]
    final_dec_file = meeting_dir / "final_release_decision.json"
    if len(out3_candidates) > 1 and final_dec_file.is_file():
        try:
            fdec = json.loads(final_dec_file.read_text(encoding="utf-8"))
            target_hash = fdec.get("artifact_manifest", {}).get("output_3_sha256")
            if target_hash:
                for cand in out3_candidates:
                    if hashlib.sha256(cand.read_bytes()).hexdigest() == target_hash:
                        return cand
        except Exception:
            pass
    return out3_file


def _load_dir_metadata(meeting_dir: Path) -> Dict[str, Any]:
    meta_file = meeting_dir / "metadata.json"
    if meta_file.is_file():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _resolve_source_text(
    meeting_dir: Path, artifacts_map: Dict[str, str], metadata: Dict[str, Any]
) -> Tuple[str, Optional[Path]]:
    if "01_transcript" in artifacts_map:
        p = Path(artifacts_map["01_transcript"])
        return p.read_text(encoding="utf-8"), p
    doc_txt = meeting_dir / "source_document.txt"
    if doc_txt.is_file():
        return doc_txt.read_text(encoding="utf-8"), doc_txt
    if metadata.get("source_text"):
        return str(metadata["source_text"]), None
    return "", None


def _build_intake_package_for_dir(
    meeting_dir: Path, out3_file: Path, metadata: Dict[str, Any]
) -> Path:
    intake_pkgs = list(meeting_dir.glob("intake_package_*.zip")) or list(
        meeting_dir.glob("urn_nee_*.zip")
    )
    if intake_pkgs:
        return intake_pkgs[0]
    from dual_pipeline.canonical_pipeline.lifecycle import (
        build_and_validate_intake_package,
    )

    artifacts_map = {"03_graph": str(out3_file)}
    for o1 in meeting_dir.glob("01_*_hitl_resolved_transcript.md"):
        artifacts_map["01_transcript"] = str(o1)
    for o2 in meeting_dir.glob("02_*_executive_recap.md"):
        artifacts_map["02_recap"] = str(o2)

    src_text, src_file_path = _resolve_source_text(meeting_dir, artifacts_map, metadata)
    return build_and_validate_intake_package(
        meeting_dir, artifacts_map, metadata, src_text, src_file_path
    )


def _save_promotion_artifacts(
    meeting_dir: Path, receipt_data: Dict[str, Any], pub_res: Any
) -> Path:
    receipt_file = meeting_dir / "backtest_promotion_receipt.json"
    receipt_file.write_text(json.dumps(receipt_data, indent=2, default=str), encoding="utf-8")
    mut_sum = (
        pub_res.get("mutation_summary")
        or (pub_res.get("ingestion_completion") or {}).get("mutation_summary")
        if isinstance(pub_res, dict)
        else None
    )
    if isinstance(mut_sum, dict) and mut_sum.get("markdown_breakdown_table"):
        breakdown_file = meeting_dir / "graph_mutation_breakdown.md"
        breakdown_file.write_text(mut_sum["markdown_breakdown_table"], encoding="utf-8")
    return receipt_file


def _backtest_promote(args: argparse.Namespace) -> Dict[str, Any]:
    """Execute backtest-promote CLI command."""
    meeting_dir_raw = getattr(args, "meeting_dir", None)
    if not meeting_dir_raw:
        raise ValueError("--meeting-dir is required for backtest-promote")

    meeting_dir = Path(meeting_dir_raw).expanduser().resolve()
    if not meeting_dir.is_dir():
        raise FileNotFoundError(f"Meeting directory does not exist: {meeting_dir}")

    out3_file = _resolve_target_output_03(meeting_dir)
    out3_data = json.loads(out3_file.read_text(encoding="utf-8"))
    metadata = _load_dir_metadata(meeting_dir)

    target_graph = Path(args.graph).resolve() if getattr(args, "graph", None) else (ROOT / "graph" / "nee_graph.sqlite")
    runtime_root = Path(args.runtime_root).resolve() if getattr(args, "runtime_root", None) else (ROOT / ".local" / "dual_pipeline")

    preview_dict, preview_md = generate_graph_delta_preview(
        output_3=out3_data,
        graph_path=target_graph,
        output_dir=meeting_dir,
        metadata=metadata,
    )
    table_deltas = preview_dict.get("table_deltas") or {
        "node": len(preview_dict.get("nodes", [])),
        "assertion": len(preview_dict.get("assertions", [])),
    }

    from dual_pipeline.hitl_governance_core.mutation_approval import request_mutation_approval
    approval = request_mutation_approval(
        action_type="BACKTEST_PROMOTE",
        target_resource=target_graph,
        table_deltas=table_deltas,
        description=f"Promote backtest output for {meeting_dir.name}",
        reviewer=getattr(args, "reviewer", None),
        rationale=getattr(args, "rationale", None),
        ledger_path=getattr(args, "ledger_path", None),
        storage_root=runtime_root,
    )

    if not approval.get("cleared"):
        print("\n" + preview_md + "\n")
        return {
            "status": approval.get("status", "AWAITING_APPROVAL"),
            "command": "backtest-promote",
            "approval_request_id": approval.get("request_id"),
            "delta_digest": approval.get("delta_digest"),
            "meeting_dir": str(meeting_dir),
            "output_03": str(out3_file),
            "preview": preview_dict,
            "graph_mutation_committed": False,
        }

    from dual_pipeline.credentials import CredentialManager
    clear_rec_existing = approval.get("clearance_record") or {}
    reviewer = (
        getattr(args, "reviewer", None)
        or clear_rec_existing.get("operator_id")
        or clear_rec_existing.get("reviewer")
        or CredentialManager.get_credential("NEE_REVIEWER")
        or metadata.get("reviewer")
        or "human_operator"
    )
    rationale = (
        getattr(args, "rationale", None)
        or clear_rec_existing.get("rationale")
        or metadata.get("rationale")
        or f"Promote backtest run from {meeting_dir.name}"
    )
    directive_id = getattr(args, "directive_id", None) or f"directive-promote-{meeting_dir.name}-{int(time.time())}"
    embedding_backend = getattr(args, "embedding_backend", None) or metadata.get("embedding_backend", "bge-m3-dev")

    pkg_path = _build_intake_package_for_dir(meeting_dir, out3_file, metadata)

    from dual_pipeline.transcript_pipeline_core import release_runner
    metadata["runtime_root"] = str(runtime_root)
    metadata["graph_path"] = str(target_graph)
    if "approval_receipt" not in metadata and "mutation_approval_receipt" not in metadata:
        clear_rec = approval.get("clearance_record") or {
            "decision_id": approval.get("request_id") or directive_id,
            "operator_id": reviewer,
            "reviewer": reviewer,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "rationale": rationale,
        }
        rcpt = {
            "request_id": approval.get("request_id") or directive_id,
            "status": "APPROVED",
            "authorized": True,
            "operator_origin": reviewer,
            "reviewer": reviewer,
            "rationale": rationale,
            "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "expected_base_generation": "current",
            "raw_clearance_record": clear_rec,
            "meeting_name": metadata.get("meeting_name") or meeting_dir.name,
        }
        metadata["approval_receipt"] = rcpt
        metadata["mutation_approval_receipt"] = rcpt

    written: Dict[str, str] = {"03_graph": str(out3_file)}
    pub_res = release_runner.publish_release_to_graph(
        package_path=pkg_path,
        metadata=metadata,
        written=written,
        directive_id=directive_id,
        reviewer=reviewer,
        rationale=rationale,
        embedding_backend=embedding_backend,
    )

    receipt_data = {
        "status": "PROMOTED",
        "command": "backtest-promote",
        "meeting_dir": str(meeting_dir),
        "output_03": str(out3_file),
        "package_path": str(pkg_path),
        "directive_id": directive_id,
        "reviewer": reviewer,
        "rationale": rationale,
        "embedding_backend": embedding_backend,
        "publish_result": pub_res,
    }
    receipt_file = _save_promotion_artifacts(meeting_dir, receipt_data, pub_res)

    return {
        "status": "COMPLETE",
        "command": "backtest-promote",
        "promoted": True,
        "meeting_dir": str(meeting_dir),
        "receipt_file": str(receipt_file),
        "publish_result": pub_res,
    }


def add_promote_fast_parser(subparsers: argparse._SubParsersAction) -> None:
    cmd = subparsers.add_parser(
        "promote-fast",
        help="Sub-second deterministic SQLite candidate-to-canonical fast-promotion with 5-point physical proof",
    )
    cmd.add_argument("--graph", type=Path, default=None, help="Target graph database path (defaults to active pair)")
    cmd.add_argument("--runtime-root", type=Path, default=None, help="Authoritative runtime root directory")
    cmd.add_argument("--source-id", type=str, default=None, help="Optional source_id filter")
    cmd.add_argument("--min-confidence", type=float, default=0.75, help="Minimum confidence threshold (default: 0.75)")
    cmd.add_argument("--approval-ref", type=str, default=None, help="Explicit governance approval reference")
    cmd.add_argument("--promote-generation", action="store_true", default=False, help="Clone and activate a new generation")
    cmd.add_argument("--dry-run", action="store_true", default=False, help="Plan promotions without mutating graph")


def _promote_fast(args: argparse.Namespace) -> Dict[str, Any]:
    from dual_pipeline.fast_promotion import fast_promote_candidates

    res = fast_promote_candidates(
        graph_path=getattr(args, "graph", None),
        runtime_root=getattr(args, "runtime_root", None),
        source_id=getattr(args, "source_id", None),
        min_confidence=float(getattr(args, "min_confidence", 0.75)),
        approval_reference=getattr(args, "approval_ref", None),
        promote_generation=bool(getattr(args, "promote_generation", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    return res.to_dict()
