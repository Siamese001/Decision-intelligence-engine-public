"""Adversarial stress and edge-case suite for Tier 1 deterministic self-healing.

Covers 4 mandatory adversarial categories under repository testing governance:
1. Boundary payloads (empty, 0-byte, deep nesting, special chars)
2. Malformed JSON streams (truncated literals, missing closures, dangling colons)
3. Parallel WAL write races (multi-threaded lock contention, stale lock recovery)
4. Invariant bypass attempts (provenance overwrite, PID immunity evasion, fake locks)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    ORIGIN_INPUT_PROVENANCE,
    resolve_person_entity,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import (
    _normalize_transcript_entities,
)
from dual_pipeline.validation.pydantic_gate import (
    DeterministicPDAParser,
    PydanticValidationError,
    repair_json_tokens,
)
from storage.connection import (
    create_sqlite_conn,
    evict_stale_session_locks,
    execute_with_retry,
    is_pid_alive,
)
from tools.pr_governance_core.common import get_immune_pids, kill_ghost_processes


class TestTier1SelfHealingAdversarial:
    """Adversarial verification across 4 mandatory testing categories."""

    # --------------------------------------------------------------------------
    # Category 1: Boundary Payloads
    # --------------------------------------------------------------------------
    def test_category1_boundary_payloads_pda_resilience(self) -> None:
        """Boundary payloads: empty, 0-byte, deep nesting, and bracketed string literals."""
        # 1. Empty / Whitespace-only payloads
        assert json.loads(repair_json_tokens("")) == {}
        assert json.loads(repair_json_tokens("   \n\t  ")) == {}

        # 2. Minimal valid JSON boundaries
        assert json.loads(repair_json_tokens("{}")) == {}
        assert json.loads(repair_json_tokens("[]")) == []

        # 3. Deeply nested object hierarchy (30 levels) truncated at the end
        nested_open = "".join(f'{{"level_{i}": ' for i in range(30)) + '{"deep_val": "ok"}'
        # Deliberately missing all 30 closing braces
        healed_nested = repair_json_tokens(nested_open)
        parsed = json.loads(healed_nested)
        curr = parsed
        for i in range(30):
            curr = curr[f"level_{i}"]
        assert curr["deep_val"] == "ok"

        # 4. String literals with JSON syntax chars (braces, brackets, commas, quotes)
        literal_payload = (
            '{"message": "Text with {braces}, [brackets], \\"escaped quotes\\", and commas,", '
            '"items": [1, 2, 3'
        )  # Missing closing bracket and brace
        healed_lit = repair_json_tokens(literal_payload)
        parsed_lit = json.loads(healed_lit)
        assert "message" in parsed_lit
        assert "{braces}" in parsed_lit["message"]
        assert len(parsed_lit["items"]) == 3

    # --------------------------------------------------------------------------
    # Category 2: Malformed JSON Streams
    # --------------------------------------------------------------------------
    def test_category2_malformed_json_streams_pda_repair(self) -> None:
        """Malformed streams: truncated boolean/null literals, dangling colons, trailing commas."""
        # 1. Truncated booleans & null
        cases = [
            ('{"active": tru', True, "active"),
            ('{"disabled": fal', False, "disabled"),
            ('{"payload": nul', None, "payload"),
        ]
        for payload, expected_val, key in cases:
            healed = repair_json_tokens(payload)
            res = json.loads(healed)
            assert res[key] is expected_val

        # 2. Dangling colons before commas and closures
        dangling_cases = [
            '{"key_a": , "key_b": "value"}',
            '{"key_a": 123, "key_b": }',
        ]
        for payload in dangling_cases:
            healed = repair_json_tokens(payload)
            res = json.loads(healed)
            assert "key_a" in res
            assert "key_b" in res

        # 3. Trailing commas in arrays and dicts
        trailing_comma = '{"list": [10, 20, 30,], "dict": {"a": 1, "b": 2,},}'
        healed_trailing = repair_json_tokens(trailing_comma)
        res_trailing = json.loads(healed_trailing)
        assert res_trailing["list"] == [10, 20, 30]
        assert res_trailing["dict"] == {"a": 1, "b": 2}

        # 4. Markdown envelope with conversational preamble and epilogue
        wrapped_payload = (
            "Here is the parsed output from the execution:\n\n"
            "```json\n"
            '{\n  "status": "success",\n  "count": 10\n}\n'
            "```\n\n"
            "Let me know if you need additional details."
        )
        healed_wrapped = repair_json_tokens(wrapped_payload)
        res_wrapped = json.loads(healed_wrapped)
        assert res_wrapped["status"] == "success"
        assert res_wrapped["count"] == 10

    # --------------------------------------------------------------------------
    # Category 3: Parallel WAL Write Races
    # --------------------------------------------------------------------------
    def test_category3_parallel_wal_write_races(self, tmp_path: Path) -> None:
        """Parallel WAL write races: 8 concurrent threads hammering SQLite under lock contention."""
        db_path = tmp_path / "adversarial_wal.sqlite"
        conn = create_sqlite_conn(db_path)
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stress_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    worker_id INTEGER NOT NULL,
                    seq INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    ts REAL NOT NULL
                );
                """
            )
        conn.close()

        num_threads = 8
        writes_per_thread = 20
        errors: List[str] = []

        def worker_task(worker_id: int) -> None:
            for seq in range(writes_per_thread):
                try:
                    c = create_sqlite_conn(db_path)
                    execute_with_retry(
                        c,
                        "INSERT INTO stress_events (worker_id, seq, data, ts) VALUES (?, ?, ?, ?);",
                        (worker_id, seq, f"payload_{worker_id}_{seq}", time.time()),
                        max_retries=5,
                        base_delay_ms=10.0,
                    )
                    c.close()
                except Exception as exc:
                    errors.append(f"Worker {worker_id} error at seq {seq}: {exc}")
                time.sleep(0.001)

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors, f"Encountered concurrency errors: {errors}"

        # Verify 100% transaction integrity
        verify_conn = create_sqlite_conn(db_path)
        cur = verify_conn.execute("SELECT count(*) as total FROM stress_events;")
        total = cur.fetchone()["total"]
        assert total == num_threads * writes_per_thread
        verify_conn.close()

    # --------------------------------------------------------------------------
    # Category 4: Invariant Bypass Attempts
    # --------------------------------------------------------------------------
    def test_category4_invariant_bypass_attempts(self, tmp_path: Path) -> None:
        """Invariant bypass attempts: roster provenance clobbering, PID immunity evasion, fake locks."""
        # 1. Roster Provenance Bypass Attempt
        # Directory default has Michael Fielder -> Apex Global Analytics
        # Operator metadata explicitly sets PartnerAdvisory
        # An adversary tries to overwrite explicit PartnerAdvisory with directory prior
        raw_text = "**Michael Fielder:** Testing roster provenance resistance."
        metadata = {
            "roster": [
                {
                    "name": "Michael Fielder",
                    "company": "PartnerAdvisory",
                    "role": "Lead Architect",
                }
            ],
            "attendees": {},
        }
        _ = _normalize_transcript_entities(raw_text, metadata)

        # Confirm provenance is locked and cannot be reverted
        assert metadata["roster"][0]["org"] == "PartnerAdvisory"
        assert metadata["roster"][0]["provenance_origin"] == ORIGIN_INPUT_PROVENANCE

        # Re-resolve with empty org - must still honor prior if explicit was stored
        locked_ent = resolve_person_entity("Michael Fielder", explicit_org="PartnerAdvisory")
        assert locked_ent is not None
        assert locked_ent.org == "PartnerAdvisory"
        assert locked_ent.provenance_origin == ORIGIN_INPUT_PROVENANCE

        # 2. Process Immunity Evasion Attempt
        # Verify caller PID, parent PID, and process group cannot be targeted
        immune_pids = get_immune_pids()
        assert os.getpid() in immune_pids
        assert os.getppid() in immune_pids

        # Attempt to run ghost process reaping against our own test directory
        test_dir = tmp_path / "victim_dir"
        test_dir.mkdir(parents=True, exist_ok=True)
        victim_file = test_dir / "protected_resource.lock"
        victim_file.write_text("lock_data")

        # Open file in current process to simulate holding handle
        with open(victim_file, "r") as handle:
            # Reaping ghost processes must NOT kill the current process
            killed = kill_ghost_processes(test_dir)
            assert os.getpid() not in killed
            assert os.getppid() not in killed

        # 3. Fake Lockfile Liveness Validation
        lock_dir = tmp_path / ".session_locks"
        lock_dir.mkdir(parents=True, exist_ok=True)

        # Create lock with living PID (current process)
        living_lock = lock_dir / "living.flock"
        living_lock.write_text(f"{os.getpid()}\n")

        # Create lock with deceased PID
        dead_lock = lock_dir / "deceased.flock"
        dead_lock.write_text("99999999\n")

        evicted = evict_stale_session_locks(lock_dir=lock_dir)

        # Must evict deceased lock, must NEVER evict living lock
        assert "deceased.flock" in evicted
        assert not dead_lock.exists()
        assert living_lock.exists()
        assert "living.flock" not in evicted
