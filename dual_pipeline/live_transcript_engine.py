"""
dual_pipeline/live_transcript_engine.py

DEPRECATED / DECOMMISSIONED:
LiveTranscriptEngine has been removed per Wave 3 of the Live Meeting Refactor Plan.
Use canonical CLI entrypoints ('python nee.py record-meeting' / 'python nee.py ingest-meeting').
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    sys.stderr.write(
        "[GOVERNANCE VIOLATION] Direct CLI execution of dual_pipeline.live_transcript_engine is strictly prohibited.\n"
        "Use the canonical CLI entrypoint instead: 'python nee.py record-meeting ...' or 'python nee.py ingest-meeting ...'\n"
    )
    sys.exit(1)


def main() -> int:
    sys.stderr.write(
        "[GOVERNANCE VIOLATION] Direct CLI execution of dual_pipeline.live_transcript_engine is strictly prohibited.\n"
    )
    return 1
