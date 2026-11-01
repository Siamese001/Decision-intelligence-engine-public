"""CLI command entrypoint to execute transcript ingestion for any declarative manifest."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.credentials import CredentialManager

CredentialManager.ensure_ssot_env_loaded()

from dual_pipeline.manifest import IngestionMeetingManifest
from dual_pipeline.manifest_runner import ManifestPipelineRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute Governed Transcript Ingestion from Manifest")
    parser.add_argument("--manifest", required=True, help="Path to declarative JSON manifest")
    parser.add_argument("--model", default=None, help="Evaluator model for LLM Multi-Judge (defaults to ModelTier.DIALECTICAL_JUDGE)")
    parser.add_argument("--reasoning-effort", default=None, help="Reasoning effort for LLM Multi-Judge")
    parser.add_argument("--fresh-eval", action="store_true", help="Bypass pinned evaluations and force fresh LLM judge calls")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    manifest_path = Path(args.manifest)
    manifest = IngestionMeetingManifest.from_json(manifest_path)
    print(f"Loaded manifest: {manifest.meeting_title} ({manifest.human_date})")

    res = ManifestPipelineRunner.run_manifest(
        manifest=manifest,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        publish_to_graph=True,
        fresh_eval=args.fresh_eval,
    )
    print(f"Execution Result: {res}")


if __name__ == "__main__":
    main()
