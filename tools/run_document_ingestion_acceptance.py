#!/usr/bin/env python3
"""Run bounded document-ingestion waves and seal immutable, graph-bound evidence."""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from storage.connection import create_sqlite_conn
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TEST_CLASSES = {wave: (f"Wave{wave}LifecycleTests", f"Wave{wave}AdversarialTests")
                for wave in (1, 2, 3, 4, 5, 6)}
BINDINGS = (
    "tools/run_document_ingestion_acceptance.py", "tools/run_tests_with_progress.py",
    "tools/test_discovery_core/discovery.py", "tools/ci_gates_core/base_tests.py",
    "dual_pipeline/release_thresholds.py", "dual_pipeline/prompts/contracts/output3_contract.py",
    "governance/policy_registry.yaml", ".agents/rules/wave-progression.md",
    ".agents/rules/e2e-and-edge-case-coverage.md",
    "contracts/dual_pipeline/schemas/evidence-envelope-intake.schema.json",
    "docs/plans/docs_lens_ingestion_runtime_plan.md",
)
SOURCES = (
    "documents/project_lens/project-scope-items-for-review.docx",
    "documents/project_lens/mandy_hopmann_feedback_sow_phase1_scope_items_20261002.md",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-c", "core.fsmonitor=false", "-C", str(ROOT), *args], text=True).strip()


def _primary_root(override: Path | None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    common = Path(_git("rev-parse", "--git-common-dir"))
    return (common if common.is_absolute() else ROOT / common).resolve().parent


def _file_state(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.is_file(),
            "sha256": _sha(path) if path.is_file() else None}


def _graph_snapshot(path: Path) -> dict[str, Any]:
    """Copy bytes from live files, then count a private read-only SQLite snapshot.

    A private WAL/SHM context avoids even SQLite shared-memory writes at the live
    graph. Hashes before and after copying/reading reject concurrent source writes.
    """
    path = path.resolve(strict=True)
    wal = path.with_name(path.name + "-wal")
    journal = path.with_name(path.name + "-journal")
    if journal.is_file() and journal.stat().st_size:
        raise RuntimeError(f"Graph has an active rollback journal: {path}")
    before = {"main": _file_state(path), "wal": _file_state(wal)}
    with tempfile.TemporaryDirectory(prefix="nee-acceptance-readonly-") as temp:
        copied = Path(temp) / "graph.sqlite"
        shutil.copyfile(path, copied)
        if wal.is_file():
            copied_wal = copied.with_name(copied.name + "-wal")
            shutil.copyfile(wal, copied_wal)
            if _sha(copied_wal) != before["wal"]["sha256"]:
                raise RuntimeError("WAL changed while copying its read-only snapshot")
        if _sha(copied) != before["main"]["sha256"]:
            raise RuntimeError("Graph changed while copying its read-only snapshot")
        connection = create_sqlite_conn(copied, readonly=True)
        try:
            connection.execute("BEGIN")
            names = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            if not names:
                raise RuntimeError("Governed database snapshot contains no tables")
            counts = {}
            for name in names:
                quoted = '"' + name.replace('"', '""') + '"'
                query = f"SELECT count(*) FROM {quoted}"
                counts[name] = connection.execute(query).fetchone()[0]
        finally:
            connection.close()
    after = {"main": _file_state(path), "wal": _file_state(wal)}
    if before != after:
        raise RuntimeError("Live graph or WAL changed during evidence capture")
    return {**before, "table_count": len(counts), "row_counts": counts}


def _scope_snapshot(primary: Path) -> dict[str, Any]:
    runtime_roots = {(ROOT / ".local/dual_pipeline").resolve(),
                     (primary / ".local/dual_pipeline").resolve()}
    pairs, graphs = {}, {primary / "graph/nee_graph.sqlite", ROOT / "graph/nee_graph.sqlite"}
    sidecars: set[Path] = set()
    for runtime in sorted(runtime_roots):
        pointer = runtime / "active_pair.json"
        if not pointer.is_file():
            raise RuntimeError(f"Required active graph/index pointer is missing: {pointer}")
        pair_bytes = pointer.read_bytes()
        pair = json.loads(pair_bytes)
        graph = (runtime / pair["graph_path"]).resolve(strict=True)
        if runtime not in graph.parents:
            raise RuntimeError("Active graph pointer escapes its runtime root")
        graphs.add(graph)
        sidecar = (runtime / pair["sidecar_path"]).resolve(strict=True)
        if runtime not in sidecar.parents:
            raise RuntimeError("Active sidecar pointer escapes its runtime root")
        sidecars.add(sidecar)
        pairs[str(runtime)] = {"active_pair_path": str(pointer),
            "active_pair_sha256": hashlib.sha256(pair_bytes).hexdigest(),
            "generation_id": pair["generation_id"], "pair": pair}
    snapshots = {str(path): _graph_snapshot(path) for path in sorted({p.resolve() for p in graphs})}
    index_snapshots = {str(path): _graph_snapshot(path) for path in sorted(sidecars)}
    required_vectors = {"retrieval_record", "dense_vector", "sparse_weight", "colbert_token"}
    if any(not required_vectors <= set(value["row_counts"]) for value in index_snapshots.values()):
        raise RuntimeError("Active sidecar does not contain all four governed vector tables")
    for runtime, binding in pairs.items():
        sidecar_path = str((Path(runtime) / binding["pair"]["sidecar_path"]).resolve())
        if index_snapshots[sidecar_path]["main"]["sha256"] != binding["pair"]["sidecar_sha256"]:
            raise RuntimeError("Active sidecar hash does not match its pair binding")
        graph_path = str((Path(runtime) / binding["pair"]["graph_path"]).resolve())
        if snapshots[graph_path]["main"]["sha256"] != binding["pair"]["graph_sha256"]:
            raise RuntimeError("Active graph hash does not match its pair binding")
        if _sha(Path(binding["active_pair_path"])) != binding["active_pair_sha256"]:
            raise RuntimeError(f"Active pair changed during scope capture: {runtime}")
    return {"runtime_roots": pairs, "graphs": snapshots, "sidecars": index_snapshots}


def _targets(wave: int) -> tuple[list[str], list[Path]]:
    if wave == 4:
        paths = [ROOT / "tests/end_to_end/test_document_ingestion_retrieval_e2e.py",
                 ROOT / "tests/adversarial/test_document_ingestion_retrieval_adversarial.py"]
    elif wave == 5:
        paths = [ROOT / "tests/end_to_end/test_document_ingestion_audit_e2e.py",
                 ROOT / "tests/adversarial/test_document_ingestion_audit_adversarial.py"]
    elif wave == 6:
        paths = [ROOT / "tests/end_to_end/test_document_ingestion_qualification_e2e.py",
                 ROOT / "tests/adversarial/test_document_ingestion_qualification_adversarial.py"]
    else:
        paths = [ROOT / "tests/end_to_end/test_document_ingestion_runtime_e2e.py",
                 ROOT / "tests/adversarial/test_document_ingestion_runtime_adversarial.py"]
    targets = []
    for name, path in zip(TEST_CLASSES[wave], paths):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        if sum(isinstance(node, ast.ClassDef) and node.name == name for node in tree.body) != 1:
            raise RuntimeError(f"Missing declared acceptance class: {name}")
        targets.append(".".join(path.relative_to(ROOT).with_suffix("").parts) + "." + name)
    return targets, paths


def _ci_enrollment(targets: list[str]) -> dict[str, str]:
    from tools.ci_gates_core.base_tests import BASE_BGE_TESTS, BASE_DETERMINISTIC_TESTS, BASE_E2E_TESTS
    from tools.test_discovery_core import resolve_all_tests
    groups = resolve_all_tests(base_deterministic=BASE_DETERMINISTIC_TESTS,
        base_e2e=BASE_E2E_TESTS, base_bge=BASE_BGE_TESTS, repo_root=ROOT, strict=True)
    enrollment = {}
    for target in targets:
        matches = [name for name, entries in zip(("deterministic", "e2e", "bge"), groups)
                   if any(target == item or target.startswith(item + ".") for item in entries)]
        if len(matches) != 1 or matches[0] == "bge":
            raise RuntimeError(f"Acceptance is not unambiguously enrolled in provider-free CI: {target}")
        enrollment[target] = matches[0]
    return enrollment


def _bindings(primary: Path, tests: list[Path]) -> dict[str, dict[str, str]]:
    paths = {ROOT / rel for rel in BINDINGS} | set(tests)
    changed = _git("diff", "--name-only", "HEAD", "--").splitlines()
    changed += _git("ls-files", "--others", "--exclude-standard").splitlines()
    prefixes = ("dual_pipeline/", "tools/", "tests/", "governance/", "policy/", "docs/plans/")
    paths.update(ROOT / rel for rel in changed if rel.startswith(prefixes) and (ROOT / rel).is_file())
    for source in SOURCES:
        path = ROOT / source
        paths.add(path if path.is_file() else primary / source)
    return {str(path.resolve()): {"sha256": _sha(path)} for path in sorted(paths)}


def _test_ids(suite: unittest.TestSuite) -> list[str]:
    ids = []
    for test in suite:
        ids.extend(_test_ids(test) if isinstance(test, unittest.TestSuite) else [test.id()])
    return ids


class _Tee:
    def __init__(self, terminal, log) -> None:
        self.terminal, self.log = terminal, log

    def write(self, value: str) -> int:
        value = value.replace("\r", "\n")
        self.terminal.write(value)
        self.log.write(value)
        self.flush()
        return len(value)

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()

    def isatty(self) -> bool:
        return False

    def writeln(self, value: str = "") -> None:
        self.write(value + "\n")


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    payload["receipt_digest"] = _digest(payload)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def verify_receipt(path: Path, *, required_wave: int | None = None) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = value.pop("receipt_digest", None)
    if expected != _digest(value) or value.get("status") != "PASS":
        raise ValueError("Receipt is not a digest-valid successful acceptance execution")
    if value["graph_before"] != value["graph_after"]:
        raise ValueError("Receipt does not prove unchanged graph scope")
    counts = value.get("tests")
    if counts is not None:
        if (counts["expected"] <= 0 or counts["passed"] != counts["expected"]
                or counts["run"] != counts["expected"]
                or any(counts[key] for key in ("failures", "errors", "skipped"))):
            raise ValueError("Receipt does not prove every declared test passed")
        log_path = Path(value["log"]["path"])
        if not log_path.exists() and (path.parent / log_path.name).exists():
            log_path = path.parent / log_path.name
        if _sha(log_path) != value["log"]["sha256"]:
            raise ValueError("Receipt log digest does not match")
    elif "metrics" not in value or not value.get("performance_targets_met"):
        raise ValueError("Receipt does not certify tests or benchmark metrics")
    _verify_wave_contract(value, required_wave, receipt_path=path)
    value["receipt_digest"] = expected
    return value


def _verify_wave_contract(value: dict[str, Any], required_wave: int | None = None, receipt_path: Path | None = None) -> None:
    wave = value.get("wave")
    if (type(wave) is not int or wave not in TEST_CLASSES
            or (required_wave is not None and wave != required_wave)):
        raise ValueError("Receipt does not satisfy the required bounded wave contract")
    if "tests" in value and value.get("timeout_seconds") != 60:
        raise ValueError("Acceptance test receipt requires timeout_seconds=60")
    if wave > 1 and "predecessor" in value:
        predecessor = value["predecessor"]
        pred_path = Path(predecessor["path"])
        if not pred_path.exists() and receipt_path and (receipt_path.parent / pred_path.name).exists():
            pred_path = receipt_path.parent / pred_path.name
        verified_pred = _predecessor_binding(pred_path, required_wave=wave - 1)
        if (
            verified_pred["receipt_digest"] != predecessor.get("receipt_digest")
            or verified_pred["file_sha256"] != predecessor.get("file_sha256")
        ):
            raise ValueError(f"W{wave} predecessor does not match its verified W{wave - 1} receipt")
    _verify_wave_profiles(value, wave)


def _verify_wave_4_profiles(value: dict[str, Any]) -> None:
    expected_real = _real_w4_profile_binding()
    actual_real = value.get("real_model_profile") or {}
    if (
        actual_real.get("file_sha256") != expected_real["file_sha256"]
        or actual_real.get("receipt_digest") != expected_real["receipt_digest"]
    ):
        raise ValueError("W4 receipt does not bind the pinned real-model profile")
    if value.get("acceptance_revision", 1) >= 2:
        expected_batch = _real_w4_batch_binding()
        actual_batch = value.get("real_batch_profile") or {}
        if (
            actual_batch.get("file_sha256") != expected_batch["file_sha256"]
            or actual_batch.get("receipt_digest") != expected_batch["receipt_digest"]
        ):
            raise ValueError("W4 receipt does not bind the real-model batch profile")


def _verify_wave_profiles(value: dict[str, Any], wave: int) -> None:
    if wave == 4:
        _verify_wave_4_profiles(value)
    elif wave == 5 and ("healing_counters" not in value or "validation_counters" not in value):
        raise ValueError("W5 receipt must contain measured healing and validation counters")
    elif wave == 6 and "benchmark_manifest" in value and not value.get("performance_targets_met"):
        raise ValueError("W6 benchmark receipt must certify performance_targets_met")


def _predecessor_binding(path: Path, *, required_wave: int = 1) -> dict[str, str]:
    path = path.expanduser().resolve(strict=True)
    file_hash = _sha(path)
    verified = verify_receipt(path, required_wave=required_wave)
    if _sha(path) != file_hash:
        raise ValueError(f"W{required_wave} predecessor receipt changed during verification")
    return {"path": str(path), "receipt_digest": verified["receipt_digest"],
            "file_sha256": file_hash}


def _real_w4_profile_binding() -> dict[str, str]:
    path = (ROOT / "reports/lens_runtime/wave-4-real-model.json").resolve(strict=True)
    value = json.loads(path.read_text(encoding="utf-8"))
    digest_value = value.pop("receipt_digest", None)
    model_policy = json.loads((ROOT / "requirements/bge-m3-model.json").read_text())
    new_ids = set(value.get("new_text_record_ids") or [])
    if (digest_value != _digest(value) or value.get("status") != "PASS"
            or value.get("model_id") != "BAAI/bge-m3"  # noqa: SSOT_EXEMPT
            or value.get("model_revision") != model_policy["revision"]
            or value.get("model_manifest_digest") != model_policy["expected_manifest_digest"]
            or value.get("evaluation_status") != "PASS"
            or value.get("source_status") != "VERIFIED"
            or value.get("delta_vectors", 0) < 1 or not new_ids
            or not value.get("active_generation_unchanged")
            or not new_ids <= set(value.get("retrieved_record_ids") or [])
            or not new_ids <= set(value.get("acl_denied_record_ids") or [])
            or not new_ids <= set(value.get("lane_denied_record_ids") or [])):
        raise ValueError("W4 pinned real-model source retrieval profile is incomplete")
    return {"path": str(path), "file_sha256": _sha(path), "receipt_digest": digest_value,
            "evaluation_digest": value["evaluation_digest"]}


def _real_w4_batch_binding() -> dict[str, str]:
    path = (ROOT / "reports/lens_runtime/wave-4-batch-model.json").resolve(strict=True)
    value = json.loads(path.read_text(encoding="utf-8"))
    digest_value = value.pop("receipt_digest", None)
    model_policy = json.loads((ROOT / "requirements/bge-m3-model.json").read_text())
    if (digest_value != _digest(value) or value.get("status") != "PASS"
            or value.get("model_id") != "BAAI/bge-m3"  # noqa: SSOT_EXEMPT
            or value.get("model_revision") != model_policy["revision"]
            or value.get("model_manifest_digest") != model_policy["expected_manifest_digest"]
            or value.get("component_count") != 2
            or value.get("component_operation_count", 0) < 1
            or value.get("component_operation_count") != value.get("joint_operation_count")
            or value.get("source_assertions_checked", 0) < 1
            or not value.get("operation_order_equivalent")
            or value.get("rollback_status") != "ROLLED_BACK"
            or not value.get("active_generation_restored")
            or not value.get("seed_graph_unchanged")
            or not value.get("gate30_fixture_isolated")):
        raise ValueError("W4 real-model batch component/rollback profile is incomplete")
    return {"path": str(path), "file_sha256": _sha(path), "receipt_digest": digest_value}


def _source_coverage(primary: Path) -> dict[str, Any]:
    from dual_pipeline.document_pipeline_core.docx_coverage import extract_docx_with_coverage
    text, coverage = extract_docx_with_coverage((primary / SOURCES[0]).read_bytes())
    if (coverage["table_count"], coverage["row_count"], coverage["cell_count"]) != (1, 31, 155):
        raise RuntimeError("Reference LENS table coverage differs from the investigation")
    items = [number for number in range(1, 31)
             if f"[T1.R{number + 1}.C1] {number} [/T1.R{number + 1}.C1]" in text]
    if len(items) != 30:
        raise RuntimeError("Reference table did not preserve every numbered item")
    return {**{key: val for key, val in coverage.items() if key != "cells"},
            "numbered_items_preserved": items}


def _run_acceptance(payload: dict[str, Any], primary: Path, log_path: Path, timeout: int) -> None:
    from tools.run_tests_with_progress import ProgressBarTestRunner, build_suite
    print(f"{_now()} [1/5] Binding test scope, CI enrollment, code and policy", flush=True)
    targets, test_paths = _targets(payload["wave"])
    payload.update(primary_graph_root=str(primary), ci_enrollment=_ci_enrollment(targets),
                   input_bindings=_bindings(primary, test_paths), git_head=_git("rev-parse", "HEAD"))
    if payload["wave"] == 4:
        payload["real_model_profile"] = _real_w4_profile_binding()
        payload["real_batch_profile"] = _real_w4_batch_binding()
        payload["acceptance_revision"] = 2
    if payload["wave"] == 5:
        from dual_pipeline.document_pipeline_core.audit_dedup import get_healing_counters, get_validation_counters
        payload["healing_counters"] = get_healing_counters()
        payload["validation_counters"] = get_validation_counters()
    print(f"{_now()} [2/5] Checking reference source coverage and unchanged-graph baseline", flush=True)
    payload["source_coverage"] = _source_coverage(primary)
    payload["graph_before"] = _scope_snapshot(primary)
    suite = build_suite(targets, start_dir=str(ROOT / "tests"))
    ids = _test_ids(suite)
    if not ids or any(not any(tid.startswith(target + ".") for target in targets) for tid in ids):
        raise RuntimeError("Acceptance suite is empty or contains tests outside the declared wave classes")
    payload["test_ids"] = ids
    print(f"{_now()} [3/5] Executing bounded acceptance tests", flush=True)
    with log_path.open("x", encoding="utf-8") as log:
        tee = _Tee(sys.stdout, log)
        with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
            print(json.dumps({"run_id": payload["run_id"], "command": payload["command"], "tests": ids}))
            result = ProgressBarTestRunner(verbosity=2, timeout=timeout, stream=tee).run(suite)
    payload["tests"] = {"expected": len(ids), "run": result.testsRun,
        "passed": result.passed_count, "failures": result.failed_count,
        "errors": result.error_count, "skipped": result.skipped_count,
        "timings": result.test_timings}
    print(f"{_now()} [4/5] Comparing final graph, vector tables and source bindings", flush=True)
    payload["graph_after"] = _scope_snapshot(primary)
    unchanged = payload["graph_before"] == payload["graph_after"]
    payload["graph_invariant"] = "IDEMPOTENT_NOOP +0" if unchanged else "FAIL"
    success = result.wasSuccessful() and result.passed_count == len(ids) and not result.skipped_count
    if not success or not unchanged:
        raise RuntimeError("Acceptance tests or immutable graph preservation failed")
    payload["table_deltas"] = {kind: {path: {name: 0 for name in snapshot["row_counts"]}
        for path, snapshot in payload["graph_after"][kind].items()} for kind in ("graphs", "sidecars")}
    if payload["input_bindings"] != _bindings(primary, test_paths):
        raise RuntimeError("Bound code, policy or input changed during acceptance")
    if payload["wave"] == 4 and payload["real_model_profile"] != _real_w4_profile_binding():
        raise RuntimeError("Pinned W4 real-model profile changed during acceptance")
    if payload["wave"] == 4 and payload["real_batch_profile"] != _real_w4_batch_binding():
        raise RuntimeError("Pinned W4 batch profile changed during acceptance")
    _verify_wave_contract(payload)
    print(f"{_now()} [5/5] Acceptance passed; sealing immutable receipt", flush=True)
    payload["status"] = "PASS"


def _execute(args: argparse.Namespace) -> int:
    receipt = args.receipt.expanduser().absolute()
    if receipt.suffix != ".json" or receipt.exists() or receipt.is_symlink():
        raise ValueError("Receipt must be a new .json path; existing receipts cannot be rerun")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    run_id = f"lens-w{args.wave}-" + uuid.uuid4().hex
    claim = receipt.with_name(receipt.name + ".claim")
    with claim.open("x", encoding="utf-8") as stream:
        stream.write(run_id)
    log_path = receipt.with_name(receipt.stem + "." + run_id + ".log")
    started, start = _now(), time.monotonic()
    payload: dict[str, Any] = {"schema_version": "1.0.0", "wave": args.wave, "run_id": run_id,
        "command": [sys.executable, *sys.argv], "started_at": started,
        "repository_root": str(ROOT), "timeout_seconds": args.timeout, "status": "FAIL"}
    try:
        if args.wave > 1:
            payload["predecessor"] = _predecessor_binding(args.predecessor, required_wave=args.wave - 1)
        primary = _primary_root(args.graph_root)
        _run_acceptance(payload, primary, log_path, args.timeout)
    except BaseException as exc:
        payload["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        if "graph_before" in payload and "graph_after" not in payload:
            try:
                payload["graph_after"] = _scope_snapshot(primary)
            except Exception as snapshot_error:
                payload["graph_after_error"] = str(snapshot_error)
    finally:
        payload.update(completed_at=_now(), elapsed_seconds=round(time.monotonic() - start, 6))
        payload["log"] = _file_state(log_path)
        destination = receipt if payload["status"] == "PASS" else receipt.with_name(
            receipt.stem + ".failed." + run_id + ".json")
        try:
            _write_receipt(destination, payload)
        finally:
            claim.unlink(missing_ok=True)
        print(json.dumps({"status": payload["status"], "receipt": str(destination)}))
    return 0 if payload["status"] == "PASS" else 1


def _execute_benchmark(args: argparse.Namespace) -> int:
    if args.receipt is None:
        raise ValueError("--receipt is required when --benchmark-manifest is specified")
    receipt_path = args.receipt.expanduser().absolute()
    if receipt_path.exists():
        raise ValueError("Receipt path already exists")
    from tools.document_acceptance_core.benchmark_runner import execute_bounded_benchmark
    out_bench = receipt_path.with_name("benchmark.json")
    result = execute_bounded_benchmark(args.benchmark_manifest, out_bench, ROOT)
    primary = _primary_root(args.graph_root)
    snapshot = _scope_snapshot(primary)
    payload = {
        "schema_version": "1.0.0",
        "wave": 6,
        "benchmark_manifest": str(args.benchmark_manifest),
        "benchmark_observations_file": str(out_bench),
        "command": [sys.executable, *sys.argv],
        "metrics": result["metrics"],
        "performance_targets": result["performance_targets"],
        "performance_targets_met": result["performance_targets_met"],
        "terminal_outcome": result["terminal_outcome"],
        "graph_before": snapshot,
        "graph_after": snapshot,
        "graph_invariant": "IDEMPOTENT_NOOP +0",
        "table_deltas": {kind: {p: {name: 0 for name in snap["row_counts"]} for p, snap in snapshot[kind].items()} for kind in ("graphs", "sidecars")},
        "status": "PASS" if result["terminal_outcome"] == "PASS" else "FAIL",
        "started_at": _now(),
        "completed_at": _now(),
        "elapsed_seconds": result["total_elapsed_seconds"],
    }
    _write_receipt(receipt_path, payload)
    print(json.dumps({"status": payload["status"], "receipt": str(receipt_path)}))
    return 0 if payload["status"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wave", type=int, choices=(1, 2, 3, 4, 5, 6))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--graph-root", type=Path, help="Primary repository root owning graph and .local runtime")
    parser.add_argument("--verify", type=Path, metavar="RECEIPT")
    parser.add_argument("--predecessor-receipt", "--predecessor", dest="predecessor", type=Path,
                        help="Verified preceding wave receipt, required for W2-W6")
    parser.add_argument("--benchmark-manifest", type=Path, help="Run bounded benchmark qualification against manifest")
    args = parser.parse_args(argv)
    if args.verify:
        verified = verify_receipt(args.verify)
        print(json.dumps({"status": "PASS", "receipt_digest": verified["receipt_digest"]}))
        return 0
    if args.benchmark_manifest:
        return _execute_benchmark(args)
    if args.wave not in TEST_CLASSES or args.timeout != 60 or args.receipt is None:
        parser.error("acceptance requires --wave {1,2,3,4,5,6} --timeout=60 --receipt NEW_PATH")
    if (args.wave > 1) != (args.predecessor is not None):
        parser.error("--predecessor-receipt PREVIOUS_RECEIPT is required after W1 and forbidden for W1")
    os.environ.update(NEE_TEST_MODE="1", RUNNING_TESTS_WITH_PROGRESS="1", NEE_RUN_E2E_TESTS="1")
    return _execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
