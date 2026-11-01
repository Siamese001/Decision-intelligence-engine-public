import json
import os
from pathlib import Path
import time
from typing import Dict, Optional

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parent.parent


def get_timing_db_path(repo_root: Optional[Path] = None) -> Path:
    """Resolve canonical path for test_timing_db.json across .local and reports."""
    root = repo_root or ROOT
    local_path = root / ".local" / "test_timing_db.json"
    reports_path = root / "reports" / "test_timing_db.json"
    if local_path.is_file():
        return local_path
    if reports_path.is_file():
        return reports_path
    if (root / ".local").is_dir() or (root / ".git").is_file():
        return local_path
    return reports_path


class TestTimingDB:
    __test__ = False

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else get_timing_db_path()
        self._timings: Dict[str, float] = {}
        self._counts: Dict[str, int] = {}
        self._is_sqlite = self.db_path.suffix in (".sqlite", ".db")
        self._load()

    def _load(self):
        if not self.db_path.exists():
            return
        if self._is_sqlite:
            try:
                conn = create_sqlite_conn(self.db_path, readonly=True)
                with conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='test_timings'"
                    )
                    if cur.fetchone():
                        for row in cur.execute(
                            "SELECT test_id, last_duration_s, run_count FROM test_timings"
                        ):
                            self._timings[row[0]] = float(row[1])
                            self._counts[row[0]] = int(row[2])
                conn.close()
            except Exception:
                pass
        else:
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, dict):
                                self._timings[k] = float(v.get("last_duration_s", 0.0))
                                self._counts[k] = int(v.get("run_count", 1))
                            else:
                                self._timings[k] = float(v)
                                self._counts[k] = 1
            except Exception:
                self._timings = {}
                self._counts = {}

    def save(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self._is_sqlite:
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for attempt in range(10):
                try:
                    conn = create_sqlite_conn(self.db_path)
                    with conn:
                        conn.execute(
                            """
                            CREATE TABLE IF NOT EXISTS test_timings (
                                test_id TEXT PRIMARY KEY,
                                last_duration_s REAL NOT NULL,
                                run_count INTEGER NOT NULL DEFAULT 1,
                                updated_at TEXT NOT NULL
                            )
                            """
                        )
                        for test_id, duration in self._timings.items():
                            count = self._counts.get(test_id, 1)
                            conn.execute(
                                """
                                INSERT INTO test_timings (test_id, last_duration_s, run_count, updated_at)
                                VALUES (?, ?, ?, ?)
                                ON CONFLICT(test_id) DO UPDATE SET
                                    last_duration_s = excluded.last_duration_s,
                                    run_count = test_timings.run_count + 1,
                                    updated_at = excluded.updated_at
                                """,
                                (test_id, duration, count, now_iso),
                            )
                    conn.close()
                    break
                except Exception:
                    if attempt < 9:
                        time.sleep(0.05 * (attempt + 1))
                    else:
                        break
        else:
            temp_path = self.db_path.with_name(
                f"{self.db_path.stem}.tmp.{os.getpid()}.{time.time_ns()}{self.db_path.suffix}"
            )
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self._timings, f, indent=2)
                temp_path.replace(self.db_path)
            except Exception:
                if temp_path.exists():
                    temp_path.unlink()

    def record(self, test_id: str, duration: float):
        if duration < 0:
            return
        self._counts[test_id] = self._counts.get(test_id, 0) + 1
        if test_id in self._timings:
            self._timings[test_id] = 0.5 * self._timings[test_id] + 0.5 * duration
        else:
            self._timings[test_id] = duration

    def get_duration(self, test_id: str) -> float:
        return self._timings.get(test_id, 0.0)

    def sort_cases_lpt(self, cases: list) -> list:
        def key_fn(test_case):
            return self.get_duration(test_case.id())
        return sorted(cases, key=key_fn, reverse=True)

    def entry_count(self) -> int:
        return len(self._timings)
