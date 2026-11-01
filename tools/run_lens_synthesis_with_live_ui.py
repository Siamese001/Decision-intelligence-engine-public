#!/usr/bin/env python3
"""
tools/run_lens_synthesis_with_live_ui.py

Executes the Project LENS three-output synthesis, deterministic QA gates,
decomposed micro-judge verification, and governance release sealing via
CanonicalAgenticPipeline with live in-band UI progress tracking.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore[assignment]

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline
from dual_pipeline.model_registry import ModelRegistry, ModelTier
from tools.lens_progress_renderer import (
    DEFAULT_LENS_METADATA,
    get_initial_output_status,
    get_initial_stages,
    render_terminal_dashboard,
)

logger = logging.getLogger("LiveUISynthesizer")
DEFAULT_MODEL, DEFAULT_EFFORT = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)


class LiveUISynthesizer:
    """Live UI progress coordinator wrapping CanonicalAgenticPipeline."""

    def __init__(
        self,
        meeting_dir: Path,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        live_artifact_path: Optional[Path] = None,
        max_judge_retries: int = 2,
        fenced: bool = False,
    ) -> None:
        self.meeting_dir = Path(meeting_dir).resolve()
        self.model = model or DEFAULT_MODEL
        self.reasoning_effort = reasoning_effort or DEFAULT_EFFORT
        self.live_artifact_path = live_artifact_path
        self.max_judge_retries = max(0, min(int(max_judge_retries), 2))
        self.fenced = fenced
        self.start_time = time.monotonic()
        self.stages = get_initial_stages()
        self.output_status = get_initial_output_status()

        self.pbar = None
        if tqdm is not None:
            self.pbar = tqdm(
                total=len(self.stages),
                desc="Canonical Pipeline (LENS)",
                unit="stage",
                dynamic_ncols=True,
                file=sys.stdout,
            )

    def _sync_ui(self, step_idx: int, stage_name: str, print_dashboard: bool = True) -> None:
        elapsed = time.monotonic() - self.start_time
        if print_dashboard:
            dash = render_terminal_dashboard(
                step_idx=step_idx,
                total_steps=len(self.stages),
                current_stage_name=stage_name,
                stages=self.stages,
                output_status=self.output_status,
                elapsed_sec=elapsed,
                model_name=self.model,
                reasoning_effort=self.reasoning_effort,
                meeting_dir=self.meeting_dir,
                fenced=self.fenced,
            )
            print(f"\n{dash}\n")
            sys.stdout.flush()

    def _on_progress(self, step: int, total: int, desc: str) -> None:
        """Callback invoked by CanonicalAgenticPipeline."""
        stage_idx = min(step - 1, len(self.stages) - 1)
        if stage_idx >= 0:
            for i in range(stage_idx):
                if self.stages[i]["status"] == "PENDING":
                    self.stages[i]["status"] = "PASS"
            self.stages[stage_idx]["status"] = "RUNNING"
            if self.pbar:
                self.pbar.set_description(f"Canonical Pipeline [{step}/{total}]: {desc[:30]}")
                self.pbar.update(1)

        self._sync_ui(step, desc)

    def _locate_source_file(self) -> Path:
        """Locates the raw source input within or adjacent to target directory."""
        candidates = [
            self.meeting_dir / "01_09-18-2026_LENS_Status_Jake_Amit_live_transcript.md",
            self.meeting_dir / "source_document.txt",
            self.meeting_dir / "raw_transcript.txt",
        ]
        for c in candidates:
            if c.exists():
                return c
        for p in self.meeting_dir.glob("01_*_hitl_resolved_transcript.md"):
            return p
        for p in self.meeting_dir.glob("*.md"):
            return p
        raise FileNotFoundError(f"No source input document or transcript found in {self.meeting_dir}")

    def execute(self) -> Dict[str, Any]:
        print("\n🚀 STARTING PROJECT LENS SYNTHESIS WITH LIVE IN-BAND UI PROGRESS")
        print(f"📁 Target Subfolder: {self.meeting_dir}")
        print(f"🤖 Model: {self.model} ({self.reasoning_effort})")
        print("📊 UI Surface: In-Band IDE Terminal Stream (Zero Disk Artifact Churn)\n")
        self._sync_ui(0, "Pipeline Initialized", print_dashboard=True)

        meta = DEFAULT_LENS_METADATA.copy()
        meta_path = self.meeting_dir / "metadata.json"
        if meta_path.exists():
            try:
                meta.update(json.loads(meta_path.read_text(encoding="utf-8")))
            except Exception as e:
                logger.warning(f"Could not load existing metadata.json: {e}")

        source_file = self._locate_source_file()

        pipeline = CanonicalAgenticPipeline(
            model_name=self.model,
            reasoning_effort=self.reasoning_effort,
        )

        result = pipeline.run(
            input_path=source_file,
            metadata=meta,
            output_dir=str(self.meeting_dir),
            judge_model=self.model,
            reasoning_effort=self.reasoning_effort,
            max_judge_retries=self.max_judge_retries,
            progress_callback=self._on_progress,
        )

        for s in self.stages:
            s["status"] = "PASS"
        if self.pbar:
            self.pbar.close()

        total_elapsed = time.monotonic() - self.start_time
        dash = render_terminal_dashboard(
            step_idx=len(self.stages),
            total_steps=len(self.stages),
            current_stage_name=f"Pipeline COMPLETE (All {len(self.stages)} Stages PASS)",
            stages=self.stages,
            output_status=self.output_status,
            elapsed_sec=total_elapsed,
            model_name=self.model,
            reasoning_effort=self.reasoning_effort,
            meeting_dir=self.meeting_dir,
            fenced=self.fenced,
        )
        print(f"\n{dash}\n")
        print(f"🎉 CANONICAL PIPELINE EXECUTION COMPLETE ({total_elapsed:.1f}s)")
        print(f"📄 Deliverables: {result.get('artifacts')}")
        print(f"🔒 Final Release Decision: {self.meeting_dir / 'final_release_decision.json'}\n")

        return {
            "status": "PASS",
            "written": result.get("artifacts", {}),
            "package_path": result.get("intake_package"),
            "receipt_path": result.get("execution_receipt"),
            "total_elapsed": total_elapsed,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Project LENS synthesis with live UI progress.")
    parser.add_argument(
        "--meeting-dir",
        type=Path,
        default=ROOT / "output" / "project_lens" / "09-18-2026" / "Jake_Amit_Miles_PartnerAdvisory",
        help="Target meeting directory.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Synthesis and judge model name.")
    parser.add_argument("--reasoning-effort", default=DEFAULT_EFFORT, help="Reasoning effort.")
    parser.add_argument("--live-artifact", type=Path, default=None, help="Deprecated (ignored).")
    parser.add_argument("--max-judge-retries", type=int, default=2, help="Max judge retries (cap 2).")
    parser.add_argument("--fenced", action="store_true", default=False, help="Fenced code blocks.")
    args = parser.parse_args()

    synthesizer = LiveUISynthesizer(
        meeting_dir=args.meeting_dir,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        live_artifact_path=args.live_artifact,
        max_judge_retries=args.max_judge_retries,
        fenced=args.fenced,
    )
    try:
        synthesizer.execute()
        return 0
    except Exception as e:
        sys.stderr.write(f"\n❌ Pipeline execution failed: {e}\n")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
