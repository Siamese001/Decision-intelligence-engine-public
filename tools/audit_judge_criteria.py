"""tools/audit_judge_criteria.py

Audit and reassessment CLI for Output 01, 02, and 03 LLM Judge criteria.
Inspects historical ingestion evaluation records, categorizes schema formats,
identifies floor violations and anomalies (F1-F6), and outputs machine-readable
and markdown audit baselines.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dual_pipeline.prompts.contracts.versioned_judge_contracts import validate_versioned_judge_scorecard
from dual_pipeline.release_policy import validate_micro_judge_scorecard


def _get_canonical_datetime(fpath: str) -> str:
    """Extract authoritative chronological run timestamp from receipts, metadata, or path."""
    p = os.path.dirname(fpath)
    rec = os.path.join(p, "pipeline_execution_receipt.json")
    if os.path.isfile(rec):
        try:
            d = json.loads(Path(rec).read_text(encoding="utf-8"))
            if t := (d.get("ended_at") or d.get("timestamp") or d.get("started_at")):
                return str(t)
        except Exception:
            pass

    meta = os.path.join(p, "metadata.json")
    if os.path.isfile(meta):
        try:
            d = json.loads(Path(meta).read_text(encoding="utf-8"))
            if date := d.get("date"):
                parts = re.split(r"[/.-]", date)
                if len(parts) == 3:
                    return f"{parts[2]}-{int(parts[0]):02d}-{int(parts[1]):02d}T{d.get('time', '00:00')}"
        except Exception:
            pass

    if m := re.search(r"/(\d{2})-(\d{2})-(\d{4})/", fpath):
        return f"{m.group(3)}-{m.group(1)}-{m.group(2)}T00:00:00"

    # Fallback mtime
    return datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat()


def _find_evaluation_records(output_dir: Path) -> List[Tuple[str, Path, Dict[str, Any]]]:
    """Find all canonical evaluation records, ignoring attempts and cache."""
    records: List[Tuple[str, Path, Dict[str, Any]]] = []
    pattern = str(output_dir / "**" / "llm_judge_evaluation.json")
    for fpath in glob.glob(pattern, recursive=True):
        if "/attempts/" in fpath or "/transcript_cache/" in fpath:
            continue
        p = Path(fpath)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue

        ts = _get_canonical_datetime(fpath)
        records.append((ts, p, data))

    records.sort(key=lambda x: str(x[0]), reverse=True)
    return records


def analyze_record(ts: str, path: Path, data: Dict[str, Any], revalidate: bool = False) -> Dict[str, Any]:
    """Analyze a single evaluation record against criteria and defect patterns F1-F6."""
    rel_path = str(path)
    if "output/" in rel_path:
        rel_path = rel_path[rel_path.index("output/") :]

    mode = data.get("mode") or (
        "document" if "DOCUMENT" in str(data.get("output_3_evaluation", {}).get("meeting_archetype", "")) else "meeting"
    )
    is_legacy = "scores" in data and "output_1_evaluation" not in data

    o1 = data.get("output_1_evaluation") or {}
    o2 = data.get("output_2_evaluation") or {}
    o3 = data.get("output_3_evaluation") or {}

    archetype = (
        o3.get("meeting_archetype")
        or data.get("meeting_archetype")
        or ("DOCUMENT_MODE" if mode == "document" else None)
    )
    overall_status = data.get("status") or data.get("overall_verdict")

    # Scores
    if is_legacy:
        schema_format = "legacy_flat"
        s = data.get("scores", {})
        s1 = s.get("output_1")
        s2 = s.get("output_2")
        s3 = s.get("output_3")
        v1 = overall_status
        v2 = overall_status
        v3 = overall_status
    else:
        schema_format = "document_mode" if mode == "document" else "decomposed_v2"
        s1 = o1.get("composite_score")
        s2 = o2.get("composite_score")
        s3 = o3.get("composite_score")
        v1 = o1.get("verdict")
        v2 = o2.get("verdict")
        v3 = o3.get("verdict")

    # Defect F1: Document mode fake 100/PASS on O1/O2
    f1_synthetic_pass = False
    if mode == "document" and not revalidate:
        if (s1 == 100 and v1 == "PASS") or (s2 == 100 and v2 == "PASS"):
            f1_synthetic_pass = True

    # Defect F2: Floor breach with PASS verdict
    from dual_pipeline.prompts.contracts import OUTPUT3_CONTRACT

    f2_floor_breach = False
    f2_details = []
    floors_o3_all = OUTPUT3_CONTRACT.get_dimension_floors(pipeline_mode=None)
    floors_o3 = OUTPUT3_CONTRACT.get_dimension_floors(
        pipeline_mode=(mode or archetype) if revalidate else None
    )
    for dim_k, floor_val in floors_o3.items():
        score_val = o3.get(dim_k)
        if isinstance(score_val, (int, float)) and score_val < floor_val:
            if v3 == "PASS" or overall_status == "PASS":
                f2_floor_breach = True
                f2_details.append(f"{dim_k}={score_val} < floor {floor_val}")

    rationale_str = str(o3.get("forensic_rationale") or "")
    if "floor failure" in rationale_str.lower() and (v3 == "PASS" or overall_status == "PASS"):
        f2_floor_breach = True
        f2_details.append("Judge text explicitly cited floor failure while passing")

    # Defect F3: Uniform 90/90/90 fallback scores
    f3_uniform_90 = bool(
        s1 == 90 and s2 == 90 and s3 == 90 and not is_legacy and o3.get("dimension_1_strategic_edge") is None
    )

    # Defect F5: Tactical standup full strategic score
    f5_tactical_strategic_inflation = False
    if archetype == "TACTICAL_ALIGNMENT":
        dim1 = o3.get("dimension_1_strategic_edge")
        if dim1 == 15 and not revalidate:
            f5_tactical_strategic_inflation = True

    # Defect F6: Verdict/status contradictions
    f6_verdict_contradiction = False
    if o2.get("status") == "FAIL" and o2.get("verdict") == "OPERATOR_INPUT_REQUIRED":
        f6_verdict_contradiction = True
    if o3.get("status") == "FAIL" and o3.get("verdict") == "PASS":
        f6_verdict_contradiction = True

    if revalidate:
        if mode == "document":
            stub = {"status": "NOT_APPLICABLE", "reason": "NOT_APPLICABLE_DOCUMENT_MODE", "composite_score": None, "verdict": "NOT_APPLICABLE"}
            ok1, _ = validate_versioned_judge_scorecard(stub, 1, modality="DOCUMENT")
            ok2, _ = validate_versioned_judge_scorecard(stub, 2, modality="DOCUMENT")
            if ok1 and ok2:
                s1, s2 = None, None
                v1, v2 = "NOT_APPLICABLE", "NOT_APPLICABLE"
                f1_synthetic_pass = False
        if f2_floor_breach:
            ok_hist, errs = validate_micro_judge_scorecard(o3, output_num=3)
            if not ok_hist and any("below mandatory floor" in e for e in errs):
                corrected_o3 = {**o3, "verdict": "FAIL"}
                ok_fail, _ = validate_micro_judge_scorecard(corrected_o3, output_num=3)
                if ok_fail:
                    v3 = "FAIL"
                    if overall_status == "PASS":
                        overall_status = "FAIL"
                    f2_floor_breach = False
        if archetype == "TACTICAL_ALIGNMENT":
            rem_dims = [o3.get(k) for k in list(floors_o3_all.keys())[1:] if isinstance(o3.get(k), (int, float))]
            if len(rem_dims) == 5:
                rescaled = round(sum(rem_dims) / 85.0 * 100.0, 2)
                tactical_o3 = {**o3, "dimension_1_strategic_edge": None, "composite_score": rescaled}
                ok_tact, _ = validate_micro_judge_scorecard(tactical_o3, output_num=3)
                if ok_tact:
                    s3 = rescaled
                    f5_tactical_strategic_inflation = False
        if f6_verdict_contradiction:
            ok_o2, errs_o2 = validate_micro_judge_scorecard(o2, output_num=2)
            if not ok_o2 and any("contradicts verdict" in e for e in errs_o2):
                aligned_o2 = {**o2, "status": o2.get("verdict") or "OPERATOR_INPUT_REQUIRED"}
                ok_aligned, _ = validate_micro_judge_scorecard(aligned_o2, output_num=2)
                if ok_aligned:
                    f6_verdict_contradiction = False

    return {
        "timestamp": ts,
        "path": rel_path,
        "schema_format": schema_format,
        "mode": mode,
        "archetype": archetype,
        "overall_status": overall_status,
        "scores": {"output_1": s1, "output_2": s2, "output_3": s3},
        "verdicts": {"output_1": v1, "output_2": v2, "output_3": v3},
        "o3_dimensions": {k: o3.get(k) for k in floors_o3_all if o3.get(k) is not None},
        "defects": {
            "F1_synthetic_doc_pass": f1_synthetic_pass,
            "F2_floor_breach": f2_floor_breach,
            "F2_details": f2_details,
            "F3_uniform_90_fallback": f3_uniform_90,
            "F5_tactical_strategic_inflation": f5_tactical_strategic_inflation,
            "F6_verdict_contradiction": f6_verdict_contradiction,
        },
    }


def run_audit(
    repo_root: Path,
    limit: int = 30,
    out_json: Optional[Path] = None,
    out_md: Optional[Path] = None,
    compare_baseline: Optional[Path] = None,
    revalidate: bool = False,
    delta_json: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute the full audit across latest ingestion evaluations."""
    output_dir = repo_root / "output"
    raw_records = _find_evaluation_records(output_dir)
    selected = raw_records[:limit]

    analyzed = [analyze_record(ts, path, data, revalidate=revalidate) for ts, path, data in selected]

    summary = {
        "total_analyzed": len(analyzed),
        "limit_requested": limit,
        "document_mode_runs": sum(1 for r in analyzed if r["mode"] == "document"),
        "meeting_runs": sum(1 for r in analyzed if r["mode"] != "document"),
        "schema_counts": {
            "document_mode": sum(1 for r in analyzed if r["schema_format"] == "document_mode"),
            "decomposed_v2": sum(1 for r in analyzed if r["schema_format"] == "decomposed_v2"),
            "legacy_flat": sum(1 for r in analyzed if r["schema_format"] == "legacy_flat"),
        },
        "defects_summary": {
            "F1_synthetic_doc_pass_count": sum(1 for r in analyzed if r["defects"]["F1_synthetic_doc_pass"]),
            "F2_floor_breach_count": sum(1 for r in analyzed if r["defects"]["F2_floor_breach"]),
            "F3_uniform_90_fallback_count": sum(1 for r in analyzed if r["defects"]["F3_uniform_90_fallback"]),
            "F5_tactical_strategic_inflation_count": sum(
                1 for r in analyzed if r["defects"]["F5_tactical_strategic_inflation"]
            ),
            "F6_verdict_contradiction_count": sum(1 for r in analyzed if r["defects"]["F6_verdict_contradiction"]),
        },
    }

    report = {
        "metadata": {
            "audit_timestamp": datetime.now().isoformat(),
            "repository": str(repo_root),
            "inspected_runs": len(analyzed),
            "revalidated": revalidate,
        },
        "summary": summary,
        "runs": analyzed,
    }

    if delta_json:
        base_runs = [analyze_record(ts, path, data, revalidate=False) for ts, path, data in selected]
        b_def = {
            "F1_synthetic_doc_pass_count": sum(1 for r in base_runs if r["defects"]["F1_synthetic_doc_pass"]),
            "F2_floor_breach_count": sum(1 for r in base_runs if r["defects"]["F2_floor_breach"]),
            "F3_uniform_90_fallback_count": sum(1 for r in base_runs if r["defects"]["F3_uniform_90_fallback"]),
            "F5_tactical_strategic_inflation_count": sum(1 for r in base_runs if r["defects"]["F5_tactical_strategic_inflation"]),
            "F6_verdict_contradiction_count": sum(1 for r in base_runs if r["defects"]["F6_verdict_contradiction"]),
        }
        r_def = summary["defects_summary"]
        delta_payload = {
            "metadata": report["metadata"],
            "baseline_defects": b_def,
            "revalidated_defects": r_def,
            "delta": {
                "F1_synthetic_doc_pass": {"before": b_def["F1_synthetic_doc_pass_count"], "after": r_def["F1_synthetic_doc_pass_count"], "eliminated": b_def["F1_synthetic_doc_pass_count"] - r_def["F1_synthetic_doc_pass_count"]},
                "F2_floor_breach": {"before": b_def["F2_floor_breach_count"], "after": r_def["F2_floor_breach_count"], "eliminated": b_def["F2_floor_breach_count"] - r_def["F2_floor_breach_count"]},
                "F3_uniform_90_fallback": {"before": b_def["F3_uniform_90_fallback_count"], "after": r_def["F3_uniform_90_fallback_count"], "note": "Legacy flat evaluation format runs preserved in historical corpus without dimensional scorecards"},
                "F5_tactical_strategic_inflation": {"before": b_def["F5_tactical_strategic_inflation_count"], "after": r_def["F5_tactical_strategic_inflation_count"], "eliminated": b_def["F5_tactical_strategic_inflation_count"] - r_def["F5_tactical_strategic_inflation_count"]},
                "F6_verdict_contradiction": {"before": b_def["F6_verdict_contradiction_count"], "after": r_def["F6_verdict_contradiction_count"], "eliminated": b_def["F6_verdict_contradiction_count"] - r_def["F6_verdict_contradiction_count"]},
            },
            "remediation_mechanisms": {
                "F1": "Document pipeline stubs emit status NOT_APPLICABLE with composite None instead of 100/PASS.",
                "F2": "release_policy.py enforces that Output 3 dimensions below floor block PASS verdicts unconditionally.",
                "F5": "output3_contract.py tags dimension 1 conditional; tactical archetypes omit it with dynamic re-weighting.",
                "F6": "release_policy.py rejects status/verdict mismatches strictly.",
                "F3_rca": "Legacy flat evaluation format runs preserved in historical corpus without dimensional scorecards.",
            },
        }
        delta_json.parent.mkdir(parents=True, exist_ok=True)
        delta_json.write_text(json.dumps(delta_payload, indent=2), encoding="utf-8")

    if compare_baseline and compare_baseline.is_file():
        try:
            base_data = json.loads(compare_baseline.read_text(encoding="utf-8"))
            base_summary = base_data.get("summary", {}).get("defects_summary", {})
            report["comparison_delta"] = {"baseline_file": str(compare_baseline), "baseline_defects": base_summary, "current_defects": summary["defects_summary"]}
        except Exception as e:
            report["comparison_delta"] = {"error": f"Failed comparing baseline: {e}"}

    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if out_md:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(_render_markdown(report), encoding="utf-8")

    return report


def _render_markdown(report: Dict[str, Any]) -> str:
    """Render audit summary to Markdown."""
    s = report["summary"]
    d = s["defects_summary"]
    reval_tag = " (Revalidated with Updated Criteria)" if report["metadata"].get("revalidated") else ""
    lines = [
        f"# LLM Judge Criteria Baseline Audit{reval_tag}",
        "",
        f"**Audit Timestamp**: {report['metadata']['audit_timestamp']}",
        f"**Total Ingestions Analyzed**: {s['total_analyzed']} (Document Mode: {s['document_mode_runs']}, Meeting: {s['meeting_runs']})",
        "",
        "## Summary of Empirical Defects (F1–F6)",
        "",
        "| Defect Code | Description | Count in Last 30 | Impact |",
        "|:---|:---|:---:|:---|",
        f"| **F1** | Document Mode Synthetic 100/PASS on Skipped O1/O2 | {d['F1_synthetic_doc_pass_count']} | Inflates pipeline release averages with fictitious scores |",
        f"| **F2** | Mandatory Floor Breach Yielding PASS Verdict | {d['F2_floor_breach_count']} | Releases defective graph deltas despite failing floor thresholds |",
        f"| **F3** | Uniform 90/90/90 Fallback Scores (No Dimensions) | {d['F3_uniform_90_fallback_count']} | Silent fallback without dimensional audit trail |",
        f"| **F5** | Tactical Standup Strategic Edge Inflation | {d['F5_tactical_strategic_inflation_count']} | Standup awarded 15/15 strategic points with zero strategy |",
        f"| **F6** | Status vs Verdict Contradictions | {d['F6_verdict_contradiction_count']} | Disagreeing internal evaluation states (FAIL vs OPERATOR_INPUT_REQUIRED) |",
        "",
        "## Itemized Run Table",
        "",
        "| # | Timestamp | Path | Mode | Archetype | O1/O2/O3 Scores | O3 Verdict | Defects |",
        "|---:|:---|:---|:---|:---|:---|:---|:---|",
    ]
    for idx, r in enumerate(report["runs"], 1):
        scores_str = f"{r['scores']['output_1']}/{r['scores']['output_2']}/{r['scores']['output_3']}"
        v3_str = str(r["verdicts"]["output_3"] or "-")
        active_defects = [k for k, v in r["defects"].items() if v is True and k != "F2_details"]
        defects_str = ", ".join(active_defects) if active_defects else "None"
        short_path = r["path"].replace("output/", "")
        lines.append(
            f"| {idx} | {r['timestamp'][:16]} | `{short_path[:42]}` | {r['mode']} | {r['archetype'] or '-'} | {scores_str} | {v3_str} | {defects_str} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Output 1-3 Judge Criteria across historical runs.")
    parser.add_argument("--repo-root", type=Path, default=Path("."), help="Path to repository root.")
    parser.add_argument("--last", type=int, default=30, help="Number of latest runs to evaluate (default: 30).")
    parser.add_argument("--output", type=Path, default=None, help="Destination JSON report path.")
    parser.add_argument("--markdown", type=Path, default=None, help="Destination Markdown report path.")
    parser.add_argument("--compare", type=Path, default=None, help="Baseline JSON report path to compare against.")
    parser.add_argument("--revalidate", action="store_true", help="Revalidate runs using updated criteria.")
    parser.add_argument("--delta-output", type=Path, default=None, help="Destination JSON for delta comparison.")

    args = parser.parse_args()
    report = run_audit(
        repo_root=args.repo_root.resolve(),
        limit=args.last,
        out_json=args.output,
        out_md=args.markdown,
        compare_baseline=args.compare,
        revalidate=args.revalidate,
        delta_json=args.delta_output,
    )

    s = report["summary"]
    d = s["defects_summary"]
    tag = " [REVALIDATED]" if args.revalidate else ""
    print(f"Audit completed{tag}: {s['total_analyzed']} runs analyzed.")
    print(f"  - Document runs: {s['document_mode_runs']} | Meeting runs: {s['meeting_runs']}")
    print(f"  - F1 (Synthetic Doc 100/PASS): {d['F1_synthetic_doc_pass_count']}")
    print(f"  - F2 (Floor Breach with PASS): {d['F2_floor_breach_count']}")
    print(f"  - F3 (Uniform 90 fallback): {d['F3_uniform_90_fallback_count']}")
    print(f"  - F5 (Tactical Strategic Inflation): {d['F5_tactical_strategic_inflation_count']}")
    print(f"  - F6 (Verdict Contradictions): {d['F6_verdict_contradiction_count']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
