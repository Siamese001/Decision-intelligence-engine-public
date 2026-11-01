from typing import Any, Dict, List


def reconcile_transcript_coverage(expected_duration: float, transcript_segments: List[Dict[str, Any]], min_coverage: float = 0.85) -> None:
    """
    Validates that the provided transcript segments cover at least min_coverage percent
    of the expected total audio duration. Raises RuntimeError if validation fails.
    """
    if expected_duration <= 0:
        return

    if not transcript_segments:
        raise RuntimeError(f"Fail-Fast: Transcript is empty but expected audio duration is {expected_duration}s.")

    start_time = min((s.get("start", s.get("start_sec", 0.0)) for s in transcript_segments), default=0.0)
    end_time = max((s.get("end", s.get("end_sec", 0.0)) for s in transcript_segments), default=0.0)
    covered_duration = end_time - start_time

    coverage = covered_duration / expected_duration
    if coverage < min_coverage:
        raise RuntimeError(
            f"Fail-Fast: Insufficient transcript coverage. "
            f"Expected {expected_duration}s but covered {covered_duration}s ({coverage*100:.1f}%). "
            f"Minimum required is {min_coverage*100:.1f}%."
        )
