#!/usr/bin/env python3
"""Analyze Codex rollout token snapshots without emitting conversation content."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


def _token_snapshot(event: Mapping[str, Any]) -> dict[str, int] | None:
    if event.get("type") != "event_msg":
        return None
    payload = event.get("payload")
    if not isinstance(payload, Mapping) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, Mapping):
        return None
    usage = info.get("total_token_usage")
    if not isinstance(usage, Mapping):
        return None

    def token(name: str) -> int:
        value = usage.get(name, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    input_tokens = token("input_tokens")
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": min(token("cached_input_tokens"), input_tokens),
        "output_tokens": token("output_tokens"),
        "reasoning_output_tokens": token("reasoning_output_tokens"),
        "total_tokens": token("total_tokens"),
    }


def last_rollout_usage(path: Path) -> dict[str, int]:
    """Return the final cumulative token snapshot from one rollout."""

    latest: dict[str, int] | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL") from exc
            if not isinstance(value, Mapping):
                continue
            snapshot = _token_snapshot(value)
            if snapshot is not None:
                latest = snapshot
    if latest is None:
        raise ValueError(f"{path}: no cumulative token_count snapshot found")
    return latest


def analyze_rollouts(
    paths: Iterable[Path],
    *,
    warn_read_write_ratio: float = 20.0,
    block_read_write_ratio: float = 100.0,
    warn_cached_share: float = 0.8,
    block_cached_share: float = 0.95,
) -> dict[str, Any]:
    """Aggregate one final snapshot per rollout and classify the G20 posture."""

    snapshots = [last_rollout_usage(Path(path)) for path in paths]
    if not snapshots:
        raise ValueError("at least one rollout path is required")
    input_tokens = sum(value["input_tokens"] for value in snapshots)
    cached_input_tokens = sum(value["cached_input_tokens"] for value in snapshots)
    output_tokens = sum(value["output_tokens"] for value in snapshots)
    uncached_input_tokens = input_tokens - cached_input_tokens
    read_write_ratio = round(input_tokens / max(output_tokens, 1), 6)
    cached_input_share = round(cached_input_tokens / max(input_tokens, 1), 6)

    if (
        read_write_ratio >= block_read_write_ratio
        or cached_input_share >= block_cached_share
    ):
        status = "BLOCK"
    elif (
        read_write_ratio >= warn_read_write_ratio
        or cached_input_share >= warn_cached_share
    ):
        status = "WARN"
    else:
        status = "ALLOW"

    actions: list[str] = []
    if read_write_ratio >= warn_read_write_ratio:
        actions.append("CHECKPOINT_AND_START_BOUNDED_STEP")
    if cached_input_share >= warn_cached_share:
        actions.append("COMPACT_COMPLETED_CONTEXT_BEFORE_NEXT_MODEL_CALL")
    if status == "BLOCK":
        actions.append("STOP_OR_REROUTE_MODEL_EXECUTION")

    return {
        "status": status,
        "rollout_count": len(snapshots),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "uncached_input_tokens": uncached_input_tokens,
        "output_tokens": output_tokens,
        "read_write_ratio": read_write_ratio,
        "cached_input_share": cached_input_share,
        "thresholds": {
            "warn_read_write_ratio": warn_read_write_ratio,
            "block_read_write_ratio": block_read_write_ratio,
            "warn_cached_share": warn_cached_share,
            "block_cached_share": block_cached_share,
        },
        "recommended_actions": actions,
        "content_emitted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report Codex rollout read/write and cached-input pressure."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--warn-read-write-ratio", type=float, default=20.0)
    parser.add_argument("--block-read-write-ratio", type=float, default=100.0)
    parser.add_argument("--warn-cached-share", type=float, default=0.8)
    parser.add_argument("--block-cached-share", type=float, default=0.95)
    args = parser.parse_args()
    report = analyze_rollouts(
        args.paths,
        warn_read_write_ratio=args.warn_read_write_ratio,
        block_read_write_ratio=args.block_read_write_ratio,
        warn_cached_share=args.warn_cached_share,
        block_cached_share=args.block_cached_share,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
