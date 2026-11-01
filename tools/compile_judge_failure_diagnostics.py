"""
CLI tool to compile judge and QA failure diagnostics from authentic meeting attempt artifacts.
Writes reports/judge_failure_diagnostics.json conforming to Wave 1 schema.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.transcript_qa_core.judge_failure_diagnostics import (
    JudgeFailureDiagnosticsCollector,
)

logger = logging.getLogger(__name__)


def compile_diagnostics(
    output_root: Path,
    dest_file: Path,
    meeting_filter: Optional[str] = None,
) -> JudgeFailureDiagnosticsCollector:
    collector = JudgeFailureDiagnosticsCollector(root_dir=dest_file.parent.parent)

    attempt_dirs: List[Path] = []
    for p in output_root.rglob("attempts/attempt_*"):
        if p.is_dir():
            if meeting_filter and meeting_filter not in str(p):
                continue
            attempt_dirs.append(p)

    attempt_dirs.sort(key=lambda d: str(d))

    for att_dir in attempt_dirs:
        try:
            # Parse attempt number from directory name
            att_num = 1
            if "attempt_" in att_dir.name:
                att_num = int(att_dir.name.split("attempt_")[-1])
            retry_cnt = max(0, att_num - 1)

            qa_file = att_dir / "qa_gates_report.json"
            if qa_file.exists():
                with open(qa_file, "r", encoding="utf-8") as f:
                    qa_data = json.load(f)
                collector.extract_from_qa_report(
                    qa_data, attempt=att_num, retry_count=retry_cnt
                )

            judge_file = att_dir / "llm_judge_evaluation.json"
            if judge_file.exists():
                with open(judge_file, "r", encoding="utf-8") as f:
                    judge_data = json.load(f)
                collector.extract_from_judge_report(
                    judge_data, attempt=att_num, retry_count=retry_cnt
                )

            # Record attempt metadata
            collector.record_attempt(
                attempt=att_num,
                outputs={"dir": str(att_dir.relative_to(output_root))},
            )
        except Exception as exc:
            logger.debug("Failed processing %s: %s", att_dir, exc)

    collector.save_report(dest_file)
    return collector


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile judge failure diagnostics.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Root output directory to scan (default: output).",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path("reports/judge_failure_diagnostics.json"),
        help="Destination JSON report path.",
    )
    parser.add_argument(
        "--meeting",
        type=str,
        default=None,
        help="Optional meeting substring filter.",
    )
    args = parser.parse_args()
    collector = compile_diagnostics(args.output_dir, args.dest, args.meeting)
    print(f"✅ Compiled {len(collector.failures)} judge failure(s) to {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
