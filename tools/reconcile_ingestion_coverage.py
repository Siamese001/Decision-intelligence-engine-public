#!/usr/bin/env python3
"""
tools/reconcile_ingestion_coverage.py

Find previously processed sources without proven active graph coverage and
prepare efficient, approval-bound resumption into an immutable recovery manifest.
Enforces INV-BUDGET-FILE-LINES (<= 400 lines).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, read_json, sha256_file, write_json  # noqa: E402


RECOVERY_CONTRACT_ID = "urn:nee:dual-pipeline:recovery-manifest:1.0.0"
STATUS_COVERED = "COVERED"
STATUS_PENDING_APPROVAL = "PENDING_APPROVAL"
STATUS_NEEDS_PUBLICATION = "NEEDS_PUBLICATION"
STATUS_NEEDS_REEVALUATION = "NEEDS_REEVALUATION"
STATUS_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
STATUS_DECLINED = "DECLINED"
STATUS_UNKNOWN = "UNKNOWN"

ALL_STATUSES = (
    STATUS_COVERED,
    STATUS_PENDING_APPROVAL,
    STATUS_NEEDS_PUBLICATION,
    STATUS_NEEDS_REEVALUATION,
    STATUS_RECONCILIATION_REQUIRED,
    STATUS_DECLINED,
    STATUS_UNKNOWN,
)


def _make_item(
    source_path: Path, source_digest: str, status: str, reason: str, action: str,
    directive_id: str | None = None, active_generation: int | None = None,
    prior_receipt_path: str | None = None, prior_receipt_digest: str | None = None,
    package_digest: str | None = None,
) -> dict[str, Any]:
    return {
        "source_path": str(source_path), "source_digest": source_digest, "status": status,
        "directive_id": directive_id, "active_generation": active_generation,
        "prior_receipt_path": prior_receipt_path, "prior_receipt_digest": prior_receipt_digest,
        "package_digest": package_digest, "reconciliation_reason": reason, "suggested_action": action,
    }


def _get_active_runtime_info(runtime_root: Path) -> dict[str, Any]:
    active_pair_path = runtime_root / "active_pair.json"
    if active_pair_path.is_file():
        try:
            data = read_json(active_pair_path)
            if isinstance(data, dict):
                return {
                    "active_generation": data.get("active_generation") or data.get("generation_id"),
                    "graph_sha256": data.get("graph_sha256"),
                }
        except Exception:
            pass
    return {"active_generation": None, "graph_sha256": None}


def _scan_receipts(runtime_root: Path) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    receipts_dir = runtime_root / "receipts"
    if receipts_dir.is_dir():
        for f in sorted(receipts_dir.glob("**/*.json")):
            try:
                data = read_json(f)
                if isinstance(data, dict) and ("receipt_type" in data or "terminal_outcome" in data):
                    data["_file_path"] = str(f)
                    receipts.append(data)
            except Exception:
                continue
    return receipts


def _scan_approval_requests(runtime_root: Path) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    req_dir = runtime_root / "approval_requests"
    if req_dir.is_dir():
        for f in sorted(req_dir.glob("*.json")):
            try:
                data = read_json(f)
                if isinstance(data, dict):
                    data["_file_path"] = str(f)
                    requests.append(data)
            except Exception:
                continue
    return requests


def _scan_journal_issues(runtime_root: Path) -> list[dict[str, Any]]:
    journal_path = runtime_root / "publication_journal.jsonl"
    issues: list[dict[str, Any]] = []
    if journal_path.is_file():
        try:
            with open(journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    if rec.get("status") in ("IN_FLIGHT", "CRASHED", "UNCERTAIN_WRITE", "FAILED_AFTER_WRITE"):
                        issues.append(rec)
        except Exception:
            pass
    pj_dir = runtime_root / "publication_journal"
    if pj_dir.is_dir():
        for intent_f in sorted(pj_dir.glob("*.intent.json")):
            res_f = intent_f.with_name(intent_f.name.replace(".intent.json", ".result.json"))
            if res_f.is_file():
                continue
            try:
                idata = read_json(intent_f)
                st = idata.get("state") if isinstance(idata, dict) else "CORRUPT_JOURNAL"
                if st in ("PENDING", "CORRUPT_JOURNAL", "PARTIAL_MUTATION_ROLLBACK_FAILED") or (st == "ROLLED_BACK" and not idata.get("zero_delta", True)):
                    bindings = idata.get("bindings") or {}
                    issues.append({
                        "status": st,
                        "directive_id": idata.get("directive_id"),
                        "source_digest": bindings.get("source_digest") or bindings.get("raw_source_sha256"),
                        "source_path": bindings.get("source_path") or bindings.get("input_path"),
                        "package_digest": bindings.get("package_digest"),
                    })
            except Exception:
                continue
    return issues


def _check_source_journal(source_path: Path, source_digest: str, journal_issues: list[dict[str, Any]], active_gen: Any) -> Optional[dict[str, Any]]:
    for issue in journal_issues:
        if issue.get("source_digest") == source_digest or issue.get("source_path") == str(source_path):
            return _make_item(
                source_path, source_digest, STATUS_RECONCILIATION_REQUIRED,
                f"Uncertain writer state or crash in journal: status={issue.get('status')}",
                "RECONCILE_AND_RECOVER",
                directive_id=issue.get("directive_id"), active_generation=active_gen,
                package_digest=issue.get("package_digest"),
            )
    return None


def _check_source_approval(source_path: Path, source_digest: str, approval_requests: list[dict[str, Any]], active_gen: Any) -> Optional[dict[str, Any]]:
    for req in approval_requests:
        req_src_digest = req.get("source_package_digest") or req.get("source_digest")
        req_src_path = req.get("source_path")
        if req_src_digest == source_digest or req_src_path in (str(source_path), source_path.name):
            req_status = str(req.get("status", "")).upper()
            baseline_gen = req.get("baseline_generation")
            file_path = req.get("_file_path")
            c_digest = req.get("canonical_digest", {}).get("value")
            pkg_digest = req.get("source_package_digest")
            d_id = req.get("directive_id")

            if req_status in ("DECLINED", "REJECTED"):
                return _make_item(
                    source_path, source_digest, STATUS_DECLINED,
                    "Operator previously declined approval for this source delta.",
                    "FRESH_OPERATOR_AUTHORIZATION_REQUIRED",
                    directive_id=d_id, active_generation=active_gen,
                    prior_receipt_path=file_path, prior_receipt_digest=c_digest, package_digest=pkg_digest,
                )
            if baseline_gen is not None and active_gen is not None and baseline_gen != active_gen:
                return _make_item(
                    source_path, source_digest, STATUS_NEEDS_REEVALUATION,
                    f"Baseline generation {baseline_gen} drifted from active generation {active_gen}.",
                    "REFRESH_AND_REEVALUATE",
                    directive_id=d_id, active_generation=active_gen,
                    prior_receipt_path=file_path, prior_receipt_digest=c_digest, package_digest=pkg_digest,
                )
            if req_status == "APPROVED":
                return _make_item(
                    source_path, source_digest, STATUS_NEEDS_PUBLICATION,
                    "Approved by operator but publication and activation incomplete.",
                    "RESUME_ACTIVE_PUBLICATION",
                    directive_id=d_id, active_generation=active_gen,
                    prior_receipt_path=file_path, prior_receipt_digest=c_digest, package_digest=pkg_digest,
                )
            return _make_item(
                source_path, source_digest, STATUS_PENDING_APPROVAL,
                "Durable approval request exists awaiting operator confirmation.",
                "PROMPT_OPERATOR_APPROVAL",
                directive_id=d_id, active_generation=active_gen,
                prior_receipt_path=file_path, prior_receipt_digest=c_digest, package_digest=pkg_digest,
            )
    return None


def _check_source_receipt(source_path: Path, source_digest: str, receipts: list[dict[str, Any]], active_gen: Any) -> Optional[dict[str, Any]]:
    for rec in receipts:
        rec_src_digest = rec.get("source_digest") or rec.get("source_package_digest")
        rec_src_path = rec.get("source_path")
        if rec_src_digest == source_digest or rec_src_path in (str(source_path), source_path.name):
            is_covered = rec.get("terminal_outcome") == "PASS" and (
                rec.get("source_coverage_verified") is True
                or rec.get("publication_coverage_verified") is True
                or rec.get("completion_status") in ("COMPLETE_UPDATED", "COMPLETE_ALREADY_PRESENT")
            )
            deltas = rec.get("delta_table_counts") or {}
            non_zero_deltas = any(v > 0 for v in deltas.values()) if isinstance(deltas, dict) else False
            mutation_outcome = rec.get("mutation_outcome")

            is_dry_or_noop = (
                rec.get("dry_run") is True
                or mutation_outcome == "IDEMPOTENT_NOOP"
                or (not non_zero_deltas and rec.get("completion_status") != "COMPLETE_ALREADY_PRESENT")
            )

            file_path = rec.get("_file_path")
            c_digest = rec.get("canonical_digest", {}).get("value")
            d_id = rec.get("directive_id")
            rec_gen = rec.get("active_generation") or active_gen

            if is_covered and not is_dry_or_noop:
                return _make_item(
                    source_path, source_digest, STATUS_COVERED,
                    "Active graph coverage verified with terminal completion receipt.",
                    "NO_ACTION_REQUIRED",
                    directive_id=d_id, active_generation=rec_gen,
                    prior_receipt_path=file_path, prior_receipt_digest=c_digest,
                    package_digest=rec.get("package_digest"),
                )
            return _make_item(
                source_path, source_digest, STATUS_PENDING_APPROVAL,
                "Earlier artifact-only dry-complete run without proven active mutation.",
                "PROMPT_OPERATOR_APPROVAL",
                directive_id=d_id, active_generation=rec_gen,
                prior_receipt_path=file_path, prior_receipt_digest=c_digest,
                package_digest=rec.get("package_digest"),
            )
    return None


def classify_source_coverage(
    source_path: Path,
    runtime_root: Path,
    active_info: dict[str, Any],
    receipts: list[dict[str, Any]],
    approval_requests: list[dict[str, Any]],
    journal_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify a single source into one of the 7 governed recovery statuses."""
    active_gen = active_info.get("active_generation")

    if not source_path.is_file():
        return _make_item(
            source_path, "0" * 64, STATUS_UNKNOWN,
            f"Source file inaccessible or missing: {source_path}",
            "VERIFY_SOURCE_PERMISSIONS", active_generation=active_gen,
        )

    try:
        source_digest = sha256_file(source_path)
    except Exception as exc:
        return _make_item(
            source_path, "0" * 64, STATUS_UNKNOWN,
            f"Failed computing source digest: {exc}",
            "VERIFY_SOURCE_PERMISSIONS", active_generation=active_gen,
        )

    res = _check_source_journal(source_path, source_digest, journal_issues, active_gen)
    if res:
        return res

    res = _check_source_approval(source_path, source_digest, approval_requests, active_gen)
    if res:
        return res

    res = _check_source_receipt(source_path, source_digest, receipts, active_gen)
    if res:
        return res

    return _make_item(
        source_path, source_digest, STATUS_PENDING_APPROVAL,
        "Source uningested; requires intake processing and operator approval.",
        "INITIATE_ACTIVE_INGESTION", active_generation=active_gen,
    )



def reconcile_coverage(
    sources: list[Path],
    runtime_root: Path,
    manifest_id: str | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Execute runtime-scoped inventory and compile an immutable recovery manifest."""
    runtime_root = runtime_root.resolve()
    active_info = _get_active_runtime_info(runtime_root)
    receipts = _scan_receipts(runtime_root)
    approval_requests = _scan_approval_requests(runtime_root)
    journal_issues = _scan_journal_issues(runtime_root)

    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {st: 0 for st in ALL_STATUSES}

    for source_path in sorted(sources):
        item = classify_source_coverage(
            source_path=source_path,
            runtime_root=runtime_root,
            active_info=active_info,
            receipts=receipts,
            approval_requests=approval_requests,
            journal_issues=journal_issues,
        )
        items.append(item)
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    now = datetime.now(timezone.utc).isoformat()
    m_id = manifest_id or f"rec-{int(datetime.now(timezone.utc).timestamp())}"

    payload: dict[str, Any] = {
        "contract_id": RECOVERY_CONTRACT_ID,
        "schema_version": "1.0.0",
        "artifact_id": m_id,
        "manifest_id": m_id,
        "runtime_root": str(runtime_root),
        "generated_at": now,
        "total_sources": len(items),
        "counts_by_status": counts,
        "items": items,
        "extensions": {},
    }

    copy_payload = dict(payload)
    copy_payload["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(copy_payload),
    }

    if output_path:
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(output_path, copy_payload)

    return copy_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile Ingestion Coverage and Emit Recovery Manifest")
    parser.add_argument("--runtime-root", type=Path, required=True, help="Target runtime root directory")
    parser.add_argument("--sources", type=Path, nargs="*", default=[], help="Individual source file paths")
    parser.add_argument("--sources-dir", type=Path, help="Directory containing source files to inventory")
    parser.add_argument("--output-manifest", type=Path, help="Destination recovery manifest JSON path")
    parser.add_argument("--manifest-id", type=str, help="Explicit manifest artifact ID")
    parser.add_argument("--json", action="store_true", help="Print manifest JSON to stdout")

    args = parser.parse_args(argv)
    runtime_root = args.runtime_root.resolve()
    if not runtime_root.is_dir():
        print(f"ERROR: Runtime root directory not found: {runtime_root}", file=sys.stderr)
        return 1

    sources: list[Path] = list(args.sources)
    if args.sources_dir and args.sources_dir.is_dir():
        for ext in ("*.txt", "*.md", "*.json", "*.pdf", "*.mp3", "*.m4a", "*.docx", "*.pptx", "*.xlsx", "*.csv", "*.eml", "*.html"):
            sources.extend(args.sources_dir.glob(ext))

    if not sources:
        print("ERROR: No sources provided via --sources or --sources-dir", file=sys.stderr)
        return 1

    manifest = reconcile_coverage(
        sources=sources,
        runtime_root=runtime_root,
        manifest_id=args.manifest_id,
        output_path=args.output_manifest,
    )

    if args.json or not args.output_manifest:
        print(json.dumps(manifest, indent=2))
    else:
        print(f"✅ Recovery manifest created: {args.output_manifest}")
        print(f"Total sources: {manifest['total_sources']} | Canonical digest: {manifest['canonical_digest']['value']}")
        for st, count in manifest["counts_by_status"].items():
            if count > 0:
                print(f"  - {st}: {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
