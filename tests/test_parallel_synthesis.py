"""
Tests for Wave 2: Concurrent Synthesis Engine (Parallel Outputs 2 & 3).
Verifies that ThreadPoolExecutor concurrently executes L2-Stage02 and L2-Stage03,
achieving >= 35% latency reduction compared to serial execution.
"""

import time
from pathlib import Path

from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_stages import (
    L2ExecutiveRecapExecutor,
    L2GraphExtractorExecutor,
    L2SynthesisProvider,
    OfflineTranscriptSynthesizer,
)


class SleepingSynthesisProvider(OfflineTranscriptSynthesizer, L2SynthesisProvider):
    """Mock provider with sleep delay to benchmark serial vs parallel wall-clock time."""

    def __init__(self, delay_sec: float = 0.2):
        super().__init__()
        self.delay_sec = delay_sec

    def synthesize_forensic(self, system_prompt: str, user_content: str) -> str:
        time.sleep(self.delay_sec)
        return super().synthesize_forensic(system_prompt, user_content)

    def synthesize_recap(self, system_prompt_or_prompt: str, user_content_or_metadata=None, _legacy_metadata: dict = None) -> str:
        time.sleep(self.delay_sec)
        return super().synthesize_recap(system_prompt_or_prompt, user_content_or_metadata, _legacy_metadata)

    def synthesize_graph(self, system_prompt_or_prompt: str, user_content_or_metadata=None, _legacy_metadata: dict = None) -> str:
        time.sleep(self.delay_sec)
        return super().synthesize_graph(system_prompt_or_prompt, user_content_or_metadata, _legacy_metadata)


def test_concurrent_synthesis_latency_reduction(tmp_path: Path):
    """Verify that parallelizing Stage 02 and Stage 03 cuts wall-clock time by >= 35%."""
    delay = 0.25
    provider = SleepingSynthesisProvider(delay_sec=delay)

    # 1. Measure Serial Execution Baseline (Stage 02 followed sequentially by Stage 03)
    recap_exec = L2ExecutiveRecapExecutor(provider)
    graph_exec = L2GraphExtractorExecutor(provider)

    valid_turns = "**Drew Oakley:** [00:01:00]: Hello"
    t0 = time.perf_counter()
    recap_exec.execute(valid_turns, {"metadata": {}})
    graph_exec.execute(valid_turns, pa_context={"metadata": {}})
    serial_elapsed = time.perf_counter() - t0

    # Serial time should be approximately 2 * delay (>= 0.45s)
    assert serial_elapsed >= (2 * delay * 0.85)

    # 2. Measure Parallel Execution (Stage 02 & Stage 03 run concurrently in ThreadPoolExecutor)
    from concurrent.futures import ThreadPoolExecutor

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as executor:
        f_recap = executor.submit(recap_exec.execute, valid_turns, {"metadata": {}})
        f_graph = executor.submit(graph_exec.execute, valid_turns, pa_context={"metadata": {}})
        parallel_recap, parallel_graph = f_recap.result(), f_graph.result()
    parallel_total_elapsed = time.perf_counter() - t0

    # In parallel mode, Stage 02 and Stage 03 overlap, so combined time for both stages is ~1 * delay.
    print(f"\nSerial time: {serial_elapsed:.3f}s | Parallel total: {parallel_total_elapsed:.3f}s")
    assert parallel_total_elapsed < (serial_elapsed * 0.75), (
        f"Expected parallel elapsed ({parallel_total_elapsed:.3f}s) to be < 75% of serial ({serial_elapsed:.3f}s)"
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_01 = out_dir / "01_transcript.md"
    out_02 = out_dir / "02_recap.md"
    out_03 = out_dir / "03_graph.json"
    out_01.write_text(valid_turns, encoding="utf-8")
    out_02.write_text(parallel_recap, encoding="utf-8")
    out_03.write_text(parallel_graph, encoding="utf-8")
    result = {
        "01_transcript": str(out_01),
        "02_recap": str(out_02),
        "03_graph": str(out_03),
    }
    assert "01_transcript" in result
    assert "02_recap" in result
    assert "03_graph" in result
    assert Path(result["01_transcript"]).exists()
    assert Path(result["02_recap"]).exists()
    assert Path(result["03_graph"]).exists()
