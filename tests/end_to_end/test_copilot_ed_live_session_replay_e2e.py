"""Wave 4 E2E Replay: Live Session ae00fe03-b63d-44fd-b21b-9a3493bfcc63 (Ed_Skyler_Amit, rows 1459-1465)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request


def _seed_real_sqlite_graph_with_competing_milestone(db_path: Path) -> None:
    """Seeds a real SQLite graph database with Ed More node and competing priority milestone assertion."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                display_name TEXT,
                node_type TEXT,
                description TEXT,
                status TEXT DEFAULT 'ACTIVE',
                active INTEGER DEFAULT 1,
                engagement_id TEXT
            );
            CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                canonical_status TEXT DEFAULT 'ACTIVE',
                retrieval_eligible INTEGER DEFAULT 1,
                materiality TEXT DEFAULT 'HIGH',
                classification TEXT DEFAULT 'CLIENT_SAFE',
                allowed_audiences_json TEXT DEFAULT '["INTERNAL", "CLIENT"]',
                confidence_score REAL DEFAULT 0.96,
                engagement_id TEXT,
                source_date_normalized TEXT,
                observed_at TEXT,
                hitl_required INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS v_canonical_edge (
                assertion_id TEXT,
                from_node_id TEXT,
                relationship_family TEXT,
                edge_type TEXT,
                to_node_id TEXT,
                materiality TEXT,
                source_id TEXT,
                hitl_required INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("person:ed_more", "Ed More", "Ed More", "person", "Principal Engineer at Apex Global Enterprises", "ACTIVE", 1, "project:lens"),
        )
        conn.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("person:daniel_schaap", "Daniel Schaap", "Daniel Schaap", "person", "Lead Engineer at Apex Global Enterprises", "ACTIVE", 1, "project:lens"),
        )
        conn.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("project:lens", "LENS", "LENS", "project", "LENS Turbine Fleet Project", "ACTIVE", 1, "project:lens"),
        )
        conn.execute(
            "INSERT INTO assertion VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "assertion-f8bc3becf4bee5f4b369281afec5",
                "person:ed_more",
                "DECIDED_TO",
                "project:lens",
                "Client Production Milestone: Ed More oversees LENS production readiness and turbine fleet rollout.",
                "ACTIVE",
                1,
                "HIGH",
                "CLIENT_SAFE",
                '["INTERNAL", "CLIENT"]',
                0.96,
                "project:lens",
                "2026-10-20T00:00:00Z",
                "2026-10-20T00:00:00Z",
                0,
            ),
        )
        conn.execute(
            "INSERT INTO v_canonical_edge VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "assertion-f8bc3becf4bee5f4b369281afec5",
                "person:ed_more",
                "DECIDED_TO",
                "DECIDED_TO",
                "project:lens",
                "HIGH",
                "src_milestone",
                0,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
    (db_path.parent / "active_pair.json").write_text(
        json.dumps({"graph_path": str(db_path), "graph_sha256": sha, "generation": "gen_ae00fe03"}),
        encoding="utf-8",
    )


def _build_session_ae00fe03_segments() -> list[dict]:
    """Builds the 244 5-second ASR segments mirroring session ae00fe03-b63d-44fd-b21b-9a3493bfcc63."""
    segments: list[dict] = []
    for idx in range(1, 245):
        sec = float(idx * 5)
        mm, ss = divmod(int(sec), 60)
        ts = f"00:{mm:02d}:{ss:02d}"
        if idx == 47:
            spk, txt = "Eduardo Juan More", "In the old project rule we had GE..."
        elif idx == 48:
            spk, txt = "Eduardo Juan More", "hardcoded at 100% accuracy in the extraction metric."
        elif idx == 49:
            spk, txt = "Eduardo Juan More", "If you do not put a field-level rule with Daniel Schaap, the metric is misleading."
        elif idx == 75:
            spk, txt = "Eduardo Juan More", "Notifications are flooding the wrong engineers across the fleet..."
        elif idx == 76:
            spk, txt = "Eduardo Juan More", "we need notification routing configurable by wind vs solar and GE turbines vs transformers."
        elif idx == 210:
            spk, txt = "Eduardo Juan More", "A single equipment nameplate has five different numbers on it..."
        elif idx == 211:
            spk, txt = "Eduardo Juan More", "so we need specific IGBT module rules with Daniel Schaap in the rule authoring UI."
        elif idx == 240:
            spk, txt = "Eduardo Juan More", "To make it right. Yeah, for sure."
        elif idx == 242:
            spk, txt = "Eduardo Juan More", "cool. Yeah, I mean, that seems pretty straightforward."
        elif idx == 244:
            spk, txt = "Eduardo Juan More", "I do have to run, I have another meeting in a minute."
        elif idx % 2 == 0:
            spk, txt = "Skyler", f"Reviewing LENS sprint item {idx}."
        else:
            spk, txt = "Amit Ayer", f"Checking deployment schedule step {idx}."
        segments.append(
            {
                "seq": idx,
                "chunk_idx": idx,
                "speaker": spk,
                "text": txt,
                "timestamp": ts,
                "start_sec": sec,
                "end_sec": sec + 4.8,
            }
        )
    return segments


def test_e2e_replay_all_6_prompts_from_session_ae00fe03(tmp_path: Path) -> None:
    """Replays all 6 operator prompts from live session ae00fe03-b63d-44fd-b21b-9a3493bfcc63 end-to-end."""
    meeting_dir = tmp_path / "output" / "project_lens" / "10-28-2026" / "Ed_Skyler_Amit"
    meeting_dir.mkdir(parents=True)

    metadata = {
        "session_id": "ae00fe03-b63d-44fd-b21b-9a3493bfcc63",
        "project": "project_lens",
        "engagement_id": "project:lens",
        "attendees": [
            {"name": "Ed More", "role": "Principal Engineer", "org": "Apex Global Enterprises"},
            {"name": "Skyler", "role": "Engineer", "org": "PartnerAdvisory"},
            {"name": "Amit Ayer", "role": "Delivery Lead", "org": "PartnerAdvisory"},
        ],
    }
    (meeting_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    segments = _build_session_ae00fe03_segments()
    seg_path = meeting_dir / "segments.jsonl"
    seg_path.write_text("\n".join(json.dumps(s) for s in segments) + "\n", encoding="utf-8")

    db_path = tmp_path / "graph.sqlite"
    _seed_real_sqlite_graph_with_competing_milestone(db_path)

    prompts_rows_1459_to_1465 = [
        "live transcript next best point to Ed to respond",
        "respond to Ed's comments from live transcript",
        "respond to ed's overall comments live transcript",
        "respond to ed's overall comments entire transcript",
        "respond to ed's overall comments entire live transcript",
        "respond to ed's overall comments entire live transcript on how we can work better together",
    ]

    for prompt in prompts_rows_1459_to_1465:
        t0 = time.perf_counter()
        args = argparse.Namespace(
            request=prompt,
            query=None,
            prompt=None,
            intent=None,
            target=None,
            topic=None,
            words=75,
            meeting_dir=str(meeting_dir),
            runtime_root=tmp_path,
            graph=str(db_path),
            graph_path=str(db_path),
            session=None,
            active=False,
            offline=False,
            graph_only=False,
            bypass_graph_cache=True,
            json=True,
        )
        res = execute_copilot_request(args)
        elapsed = time.perf_counter() - t0
        assert elapsed < 2.0, f"Prompt '{prompt}' exceeded 2.0s latency ({elapsed:.2f}s)"
        assert res["status"] == "PASS", f"Prompt '{prompt}' failed: {res}"
        cited = res.get("cited_assertion_ids", [])
        assert cited and cited[0].startswith("live_turn_"), f"Expected live_turn_* first for '{prompt}', got {cited}"

        answers_lines = (meeting_dir / "copilot_answers.jsonl").read_text(encoding="utf-8").strip().splitlines()
        latest_answer = json.loads(answers_lines[-1])
        body = (
            latest_answer.get("answer")
            or latest_answer.get("response")
            or "\n".join(latest_answer.get("speakable_lines", []))
            or json.dumps(latest_answer)
        )

        assert "SPEAKER_NOT_IN_ROSTER" not in body
        assert "Ed More, regarding Eduardo Juan More's point" not in body
        assert "Client Production Milestone:" not in body
        assert "on your how point" not in body
        assert "have to run" not in body
        assert "To make it right" not in body
        assert any(k in body for k in ("GE", "100%", "nameplate", "IGBT", "notification", "Daniel Schaap")), (
            f"Expected Ed's substantive domain points in response for '{prompt}', got: {body}"
        )
