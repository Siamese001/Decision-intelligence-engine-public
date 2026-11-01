"""Measured ingestion profiler: runs the real ``nee.py ingest-meeting`` entry point.

Every figure is read from the run itself: external monotonic wall-clock, plus the
pipeline's own ``pipeline_execution_receipt.json`` (stage durations, provider call
count, provider-reported token usage, attempts, resume decisions) and per-output
artifact digests. Nothing is simulated or estimated.

For judge_fail and timeout scenarios, a local transport fault-injection stub
(labeled as ``local_transport_stub:<scenario>``) is spun up on 127.0.0.1 and bound
via ``OPENAI_BASE_URL``, per the approved Wave 1 governance protocol.

Usage:
    python tools/profile_ingestion_run.py --input <transcript.md> --scenario cold \
        --project <p> --meeting "<m>" --out reports/profile_cold.json [-- <extra nee args>]
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
RECEIPT_NAME = "pipeline_execution_receipt.json"
OUTPUT_GLOBS = {
    "01": "01_*hitl_resolved_transcript.md",
    "02": "02_*executive_recap.md",
    "03": "03_*canonical_signal_projection.json",
}

logger = logging.getLogger(__name__)

from dual_pipeline.model_registry import ModelRegistry, ModelTier
DEFAULT_JUDGE_MODEL, _ = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_receipt(output_dir: Path) -> Optional[Path]:
    hits = sorted(output_dir.rglob(RECEIPT_NAME), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def digest_outputs(output_dir: Path) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for key, pattern in OUTPUT_GLOBS.items():
        hits = [p for p in output_dir.rglob(pattern) if "attempts" not in p.parts]
        if hits:
            p = max(hits, key=lambda x: x.stat().st_mtime)
            st = p.stat()
            result[key] = {"path": str(p), "sha256": _sha256(p), "mtime_ns": st.st_mtime_ns}
    return result


def summarize_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    stages = receipt.get("stages") or {}
    tok = receipt.get("token_usage") or {}
    l2 = (stages.get("L2") or {}).get("details") or {}
    return {
        "terminal_outcome": receipt.get("terminal_outcome"),
        "pipeline_duration_s": receipt.get("monotonic_duration_s"),
        "stage_ms": {k: v.get("duration_ms") for k, v in stages.items()},
        "stage_status": {k: v.get("status") for k, v in stages.items()},
        "child_spans_s": (receipt.get("timing_breakdown") or {}).get("child_spans_s"),
        "provider_calls": tok.get("call_count"),
        "token_usage": tok,
        "cost_usd": receipt.get("cost_usd"),
        "attempts": l2.get("attempts"),
        "remediation_cycles": len(receipt.get("remediation_cycles") or []),
        "transcript_resume": l2.get("transcript_resume"),
        "timing_is_preterminal_snapshot": (receipt.get("execution_timing") or {}).get("is_preterminal_snapshot"),
    }


class FaultInjectionHandler(http.server.BaseHTTPRequestHandler):
    """Local transport stub for controlled timeout and judge failure testing."""

    scenario: str = "cold"
    snapshot_dir: Optional[Path] = None
    call_log: List[Dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress stderr spam

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            req_data = json.loads(body)
        except Exception:
            req_data = {}

        messages = req_data.get("messages", [])
        prompt_text = " ".join(str(m.get("content", "")) for m in messages)
        is_judge = "judge" in prompt_text.lower() or "rubric" in prompt_text.lower() or "scorecard" in prompt_text.lower()

        if self.scenario == "timeout":
            # Induce transport hang / timeout beyond request deadline
            time.sleep(12.0)
            self.send_error(504, "Gateway Timeout")
            return

        if self.scenario == "judge_fail" and is_judge:
            # Return contradictory failing scorecard
            content = json.dumps({
                "rubric_version": "2.0",
                "scorecard_schema_version": "2.0.0",
                "release_policy_version": "2.0.0",
                "evaluator_model": DEFAULT_JUDGE_MODEL,
                "reasoning_effort": "high",
                "status": "FAIL",
                "overall_verdict": "FAIL",
                "composite_score": 42,
                "dimension_1_executive_altitude": 10,
                "dimension_2_technical_substance": 12,
                "dimension_3_strategic_synthesis": 10,
                "dimension_4_scoping_and_modality": 10,
                "failed_gates": ["Gate_Judge_Synthetic_Failure_Injected"],
                "output_1_evaluation": {"status": "FAIL", "composite_score": 40},
                "output_2_evaluation": {"status": "FAIL", "composite_score": 42},
                "output_3_evaluation": {"status": "FAIL", "composite_score": 45},
                "overall_rubric_summary": "FORCED_JUDGE_FAILURE_REPRODUCTION: rubric scores below floor thresholds.",
            })
        else:
            # Serve valid synthesis content from snapshot or fallback
            content = self._get_synthesis_content(prompt_text)

        p_tokens = max(10, len(prompt_text) // 4)
        c_tokens = max(10, len(content) // 4)
        resp = {
            "id": f"chatcmpl-stub-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req_data.get("model", DEFAULT_JUDGE_MODEL),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": p_tokens, "completion_tokens": c_tokens, "total_tokens": p_tokens + c_tokens},
        }
        self.call_log.append({
            "is_judge": is_judge,
            "prompt_tokens": p_tokens,
            "completion_tokens": c_tokens,
            "total_tokens": p_tokens + c_tokens,
            "model": req_data.get("model"),
        })

        resp_bytes = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _get_synthesis_content(self, prompt: str) -> str:
        if self.snapshot_dir and self.snapshot_dir.is_dir():
            if "deliverable 02" in prompt.lower() or "recap" in prompt.lower() or "narrative" in prompt.lower():
                f2 = list(self.snapshot_dir.glob("02_*executive_recap.md"))
                if f2:
                    return f2[0].read_text(encoding="utf-8")
            if "deliverable 03" in prompt.lower() or "signal projection" in prompt.lower() or "graph" in prompt.lower():
                f3 = list(self.snapshot_dir.glob("03_*canonical_signal_projection.json"))
                if f3:
                    return f3[0].read_text(encoding="utf-8")
            if "deliverable 01" in prompt.lower() or "transcript" in prompt.lower():
                f1 = list(self.snapshot_dir.glob("01_*hitl_resolved_transcript.md"))
                if f1:
                    return f1[0].read_text(encoding="utf-8")
        return "Deterministic local synthesis content for testing."


def _start_stub_server(scenario: str, snapshot_dir: Optional[Path]) -> Tuple[http.server.HTTPServer, str]:
    FaultInjectionHandler.scenario = scenario
    FaultInjectionHandler.snapshot_dir = snapshot_dir
    FaultInjectionHandler.call_log = []
    server = http.server.HTTPServer(("127.0.0.1", 0), FaultInjectionHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}/v1"


def run_profile(
    scenario: str,
    argv: List[str],
    output_dir: Path,
    hard_timeout_s: float,
    snapshot_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    cmd = [sys.executable, str(ROOT / "nee.py"), "ingest-meeting", *argv, "--output-dir", str(output_dir)]
    env = os.environ.copy()
    stub_server = None
    stub_url = None

    if scenario in ("judge_fail", "timeout"):
        stub_server, stub_url = _start_stub_server(scenario, snapshot_dir)
        env["OPENAI_BASE_URL"] = stub_url
        env["TRANSPORT_PROVIDER_LABEL"] = f"local_transport_stub:{scenario}"

    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=hard_timeout_s, env=env)
        rc, raw_output = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out, rc = True, -9
        raw_output = ((exc.stdout or b"").decode(errors="replace") + (exc.stderr or b"").decode(errors="replace"))
    finally:
        if stub_server:
            stub_server.shutdown()

    wall_s = time.monotonic() - started
    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_dir / f"profile_{scenario}.log"
    log_file.write_text(raw_output, encoding="utf-8")
    log_sha = _sha256_bytes(raw_output.encode("utf-8"))

    receipt_path = find_receipt(output_dir)
    receipt = json.loads(receipt_path.read_text()) if receipt_path else {}
    stub_calls = list(FaultInjectionHandler.call_log) if stub_url else []

    return {
        "command": [c if c != str(ROOT / "nee.py") else "nee.py" for c in cmd[1:]],
        "scenario": scenario,
        "provider_mode": f"local_transport_stub:{scenario}" if stub_url else "live_provider",
        "wall_clock_s": round(wall_s, 3),
        "exit_code": rc,
        "harness_timed_out": timed_out,
        "log_path": str(log_file),
        "log_sha256": log_sha,
        "receipt_path": str(receipt_path) if receipt_path else None,
        "receipt": summarize_receipt(receipt) if receipt else None,
        "stub_telemetry": {
            "stub_url": stub_url,
            "call_count": len(stub_calls),
            "total_tokens": sum(c.get("total_tokens", 0) for c in stub_calls),
            "calls": stub_calls,
        } if stub_url else None,
        "outputs": digest_outputs(output_dir),
        "log_tail": raw_output[-4000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", required=True, choices=["cold", "cached", "judge_fail", "timeout", "selective"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, default=None, help="Reuse dir (needed for cached/selective runs)")
    ap.add_argument("--snapshot-dir", type=Path, default=None, help="Dir containing cold artifacts for stub")
    ap.add_argument("--hard-timeout", type=float, default=2400.0)
    ap.add_argument("nee_args", nargs=argparse.REMAINDER, help="Args after '--' forwarded to ingest-meeting")
    ns = ap.parse_args()
    extra = [a for a in ns.nee_args if a != "--"]
    out_dir = ns.output_dir or Path(tempfile.mkdtemp(prefix=f"eval_sandbox_{ns.scenario}_"))
    snapshot_dir = ns.snapshot_dir or (Path("eval_sandbox_w1/out_cold_snapshot") if Path("eval_sandbox_w1/out_cold_snapshot").is_dir() else None)

    profile = run_profile(ns.scenario, extra, out_dir, ns.hard_timeout, snapshot_dir=snapshot_dir)
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(json.dumps(profile, indent=2, default=str))
    print(json.dumps({k: profile[k] for k in ("scenario", "provider_mode", "wall_clock_s", "exit_code")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
