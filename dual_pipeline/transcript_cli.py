"""transcript_cli.py — CLI entry point for the Raw Transcript Synthesis pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline
from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_stages import HITLRequiredError
from dual_pipeline.transcript_stages_core.hitl_guard import assert_no_u0_hitl_bypass

logger = logging.getLogger(__name__)

# ── HITL exit code (Decision 2) ──────────────────────────────────────────────
HITL_EXIT_CODE = 2


class GovernedArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that exits with code 1 on usage errors, reserving code 2 strictly for HITL."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _emit_hitl_json(error: HITLRequiredError) -> dict[str, Any]:
    """Emit structured JSON to stdout for machine-readable HITL surface (Decision 2 & Agent 4)."""
    import hashlib
    import secrets

    hitl_type = getattr(error, "hitl_type", "unknown")
    prompt_text = getattr(error, "prompt", None) or str(error)
    options = getattr(error, "options", []) or []
    review_contexts = getattr(error, "review_contexts", []) or []
    context_digest = getattr(error, "context_digest", "") or ""

    if hitl_type == "single_speaker_collapse":
        evidence = dict(getattr(error, "evidence", {}) or {})
        raw_context = json.dumps({
            "evidence": evidence,
            "prompt": prompt_text,
            "hitl_type": hitl_type,
        }, sort_keys=True)
        context_sha256 = str(evidence.get("subject_sha256") or hashlib.sha256(raw_context.encode("utf-8")).hexdigest())
        payload = {
            "protocol": "dual_pipeline.cli.v1",
            "status": "hitl_required",
            "reason": "single_speaker_collapse",
            "request_id": secrets.token_hex(16),
            "context_sha256": context_sha256,
            "gate": "1",
            "dry_run": False,
            "evidence": {
                "attendee_count": int(evidence.get("attendee_count", 2)),
                "total_turns": int(evidence.get("total_turns", 0)),
                "dominant_label": str(evidence.get("dominant_label", "")),
                "dominant_label_turns": int(evidence.get("dominant_label_turns", 0)),
                "max_share_bps": 8500,
            },
            "action": "ask_user_question",
        }
        print(json.dumps(payload, ensure_ascii=False))
        return payload

    # Parse required_fields from the prompt if roster_gate
    required_fields: list[str] = getattr(error, "missing_fields", []) or []
    if hitl_type == "roster_gate" and not required_fields:
        required_fields = [
            "attendee_list",
            "organization_per_attendee",
            "role_per_attendee",
            "speaking_volume_guidance",
            "supplementary_sources",
        ]

    payload = {
        "hitl_required": True,
        "hitl_type": hitl_type,
        "decision_number": 1,
        "prompt": prompt_text,
        "options": options,
        "review_contexts": review_contexts,
        "context_digest": context_digest,
        "required_fields": required_fields,
        "resume_instructions": (
            "Re-run with --metadata containing 'roster' key "
            "(list of {name, org, role}) to proceed with verified attribution."
        ),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload


def _load_metadata(metadata_path: Optional[str]) -> dict:
    """Load metadata from a JSON file, or return an empty dict."""
    if not metadata_path:
        return {}
    path = Path(metadata_path)
    if not path.is_file():
        print(
            json.dumps({
                "error": f"Metadata file not found: {metadata_path}",
                "code": "METADATA_NOT_FOUND",
            }),
            file=sys.stderr,
        )
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def _load_supplementary_sources(paths: List[str]) -> list[dict]:
    """
    Load supplementary source files (deck, notes, email thread) into a list
    of named reference dicts for injection into metadata (Decision 3).

    Each entry: {"name": <filename>, "content": <text>, "path": <abs path>}
    Non-text files (pdf, docx) are referenced by path only (content="[binary]").
    """
    TEXT_SUFFIXES = {".md", ".txt", ".text", ".markdown", ".html", ".json"}
    from dual_pipeline.core import ROOT
    from dual_pipeline.security_paths import assert_safe_source_path
    sources = []
    for raw_path in paths:
        try:
            p = assert_safe_source_path(ROOT, raw_path)
        except Exception:
            print(
                json.dumps({
                    "error": f"Supplementary source not found: {raw_path}",
                    "code": "SUPPLEMENTARY_NOT_FOUND",
                }),
                file=sys.stderr,
            )
            sys.exit(1)
        if not p.is_file():
            print(
                json.dumps({
                    "error": f"Supplementary source not found: {raw_path}",
                    "code": "SUPPLEMENTARY_NOT_FOUND",
                }),
                file=sys.stderr,
            )
            sys.exit(1)
        if p.suffix.lower() in TEXT_SUFFIXES:
            content = p.read_text(encoding="utf-8", errors="replace")
        else:
            content = "[binary — content available at path only]"
        sources.append({
            "name": p.name,
            "path": str(p.resolve()),
            "content": content,
        })
    return sources


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the transcript pipeline CLI."""
    parser = GovernedArgumentParser(
        prog="python -m dual_pipeline.transcript_pipeline",
        description=(
            "Raw Transcript Synthesis Pipeline — agentic 3-path synthesis "
            "(AUDIO / NATIVE_TEXT / UNDIARIZED_TEXT) producing three canonical artifacts."
        ),
    )
    parser.add_argument(
        "--source",
        "--input",
        "-i",
        dest="source",
        required=True,
        metavar="PATH",
        help="Path to the input transcript file (.md, .txt) or audio file (.mp3, .wav, .m4a, ...).",
    )
    parser.add_argument(
        "--metadata",
        metavar="JSON_FILE",
        default=None,
        help="Path to a JSON file containing meeting metadata. Auto-scaffolded if omitted.",
    )
    parser.add_argument("--project", type=str, default=None, help="Governed project name")
    parser.add_argument("--meeting", "--meeting-name", dest="meeting", type=str, default=None, help="Meeting title")
    parser.add_argument("--date", type=str, default=None, help="Meeting date (MM/DD/YYYY or MM-DD-YYYY)")
    parser.add_argument("--time", type=str, default=None, help="Meeting time range")
    parser.add_argument("--classification", type=str, choices=["internal", "confidential", "restricted"], default="confidential")
    parser.add_argument("--platform", type=str, default=None, help="Originating platform")
    parser.add_argument("--attendees", type=str, default=None, help="Attendee roster")
    parser.add_argument("--model", type=str, default=None, help="LLM model for synthesis")
    parser.add_argument("--judge-model", type=str, default=None, help="Model for multi-micro-judge and LLM synthesis")
    parser.add_argument("--reasoning-effort", type=str, default="high", choices=["none", "low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--presentation", type=Path, default=None, help="Optional presentation deck")
    parser.add_argument("--mutate-graph", action=argparse.BooleanOptionalAction, default=True, help="Publish sealed package into knowledge graph (default: True)")
    parser.add_argument("--auto-mutate-on-pass", action=argparse.BooleanOptionalAction, default=True, help="Automatically publish into knowledge graph when exit QA and judge pass (default: True)")
    parser.add_argument("--directive-id", default=None, help="Optional directive ID")
    parser.add_argument("--notes", "--copilot-notes", dest="notes", action="store_true", default=False, help="Notes mode")
    parser.add_argument("--document", "--output3-only", dest="document", action="store_true", default=False, help="Document mode")
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default=".",
        help="Directory to write the three output artifacts. Defaults to current directory or auto.",
    )
    _trans_choices = ["llm", "openai", "local-mlx"]
    if (
        os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("NEE_ALLOW_TEST_FIXTURES")
        or os.environ.get("RUNNING_TESTS_WITH_PROGRESS")
        or os.environ.get("NEE_TEST_MODE")
    ):
        _trans_choices.append("offline")
    parser.add_argument(
        "--provider",
        choices=_trans_choices,
        default=None,
        help="Live synthesis provider ('llm', 'openai', 'local-mlx').",
    )
    parser.add_argument(
        "--supplementary-sources",
        nargs="*",
        metavar="PATH",
        default=[],
        help="Supplementary source files to improve speaker attribution.",
    )
    parser.add_argument(
        "--augment",
        action="store_true",
        default=False,
        help="Augment existing outputs instead of replacing them if they exist.",
    )
    return parser


def _build_execution_metadata(args: argparse.Namespace) -> dict[str, Any]:
    metadata = _load_metadata(args.metadata)
    try:
        from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata
        scaffolded = scaffold_meeting_metadata(
            input_path=args.source,
            metadata_path=args.metadata,
            project=getattr(args, "project", None),
            meeting_name=getattr(args, "meeting", None),
            date_str=getattr(args, "date", None),
            attendees=getattr(args, "attendees", None),
            time_str=getattr(args, "time", None),
            classification=getattr(args, "classification", "confidential") or "confidential",
        )
        metadata = {**scaffolded, **metadata}
    except Exception:
        pass

    if getattr(args, "platform", None):
        metadata["platform"] = args.platform
    if args.supplementary_sources:
        metadata["supplementary_sources"] = _load_supplementary_sources(args.supplementary_sources)
    if getattr(args, "bypass_speaker_hitl", False):
        raise ValueError(
            "HITL cannot be removed, disabled, or bypassed in Stage U0 ingestion (--bypass-speaker-hitl). "
            "Human-In-The-Loop resolution with atomic options and confidence levels is mandatory."
        )
    assert_no_u0_hitl_bypass(metadata)
    if args.augment:
        metadata["augmentation_mode"] = True
    if getattr(args, "notes", False):
        metadata.update({
            "notes_mode": True,
            "is_notes": True,
            "skip_output_1": True,
            "outputs": ["02", "03"],
        })
        metadata.setdefault("source_type", "copilot_notes")
    return metadata


def _extract_run_kwargs(args: argparse.Namespace, pipeline: RawTranscriptPipeline) -> dict[str, Any]:
    run_kwargs: dict[str, Any] = {}
    for flag, key in [
        ("presentation", "presentation_path"),
        ("mutate_graph", "mutate_graph"),
        ("auto_mutate_on_pass", "auto_mutate_on_pass"),
        ("directive_id", "directive_id"),
        ("judge_model", "judge_model"),
        ("reasoning_effort", "reasoning_effort"),
    ]:
        val = getattr(args, flag, None)
        if val is not None:
            run_kwargs[key] = str(val) if flag == "presentation" else val

    import inspect
    sig = inspect.signature(pipeline.run)
    has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if not has_varkw:
        supported_params = set(sig.parameters.keys())
        run_kwargs = {k: v for k, v in run_kwargs.items() if k in supported_params}
    return run_kwargs


def _run_and_render_pipeline(
    pipeline: RawTranscriptPipeline,
    args: argparse.Namespace,
    metadata: dict[str, Any],
    output_dir: Path,
    run_kwargs: dict[str, Any],
) -> int:
    from dual_pipeline.hitl_governance_core.host_approval_dispatcher import check_cli_mutation_approval
    check_cli_mutation_approval(args.source, metadata, output_dir, run_kwargs)
    try:
        result = pipeline.run(
            args.source,
            metadata,
            str(output_dir),
            **run_kwargs,
        )
    except HITLRequiredError as e:
        _emit_hitl_json(e)
        return HITL_EXIT_CODE
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e), "code": "FILE_NOT_FOUND"}), file=sys.stderr)
        return 1
    except ValueError as e:
        print(json.dumps({"error": str(e), "code": "INVALID_INPUT"}), file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e), "code": "PIPELINE_ERROR"}), file=sys.stderr)
        return 1

    artifacts = result.get("artifacts", {})
    if artifacts:
        for key, path in artifacts.items():
            print(f"[{key}] {path}")
    else:
        print(json.dumps({"status": "complete", "result": result}))

    return 0


def main(argv: Optional[list] = None) -> int:
    """Main entry point for the transcript pipeline CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "document", False):
        from dual_pipeline.cli.handlers import _ingest_document
        args.input = Path(args.source)
        _ingest_document(args)
        return 0

    metadata = _build_execution_metadata(args)

    output_dir = Path(args.output_dir)
    if str(output_dir).lower() != "auto":
        output_dir.mkdir(parents=True, exist_ok=True)

    synthesis_model = getattr(args, "model", None)
    judge_model = getattr(args, "judge_model", None) or synthesis_model or resolve_tier_model_name(ModelTier.DIALECTICAL_JUDGE)
    eff_model = synthesis_model or judge_model

    provider = getattr(args, "provider", None)
    in_test = bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("NEE_ALLOW_TEST_FIXTURES")
        or os.environ.get("RUNNING_TESTS_WITH_PROGRESS")
        or os.environ.get("NEE_TEST_MODE")
    )
    if not provider:
        provider = "offline" if in_test and not synthesis_model else "llm"
    if provider == "offline" and not in_test:
        raise ValueError(
            "FAIL_CLOSED: '--provider offline' is prohibited on production CLI ingestion. "
            "Production ingestion mandates live inference ('llm', 'openai', 'local-mlx'). "
            "Deterministic mock fixtures are restricted strictly to internal test harnesses."
        )

    pipeline = CanonicalAgenticPipeline(
        provider_type=provider,
        model_name=eff_model,
        reasoning_effort=getattr(args, "reasoning_effort", "high"),
    )

    run_kwargs = _extract_run_kwargs(args, pipeline)
    return _run_and_render_pipeline(pipeline, args, metadata, output_dir, run_kwargs)



if __name__ == "__main__":
    sys.stderr.write(
        "[GOVERNANCE VIOLATION] Direct CLI execution of dual_pipeline.transcript_cli is strictly prohibited.\n"
        "All meeting transcript ingestion must execute via the canonical entrypoint: 'python nee.py ingest-meeting ...'\n"
    )
    sys.exit(1)
