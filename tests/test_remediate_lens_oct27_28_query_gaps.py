"""Wave 3 tests: Governed promotion of October 27-28 LENS candidate assertions, Michael Reiss/Rice alias registration, sidecar synchronization, and 5-check physical proof."""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import ROOT, sha256_file
from dual_pipeline.index import validate_sidecar
from dual_pipeline.query_core.graph_first_engine import GraphFirstQueryEngine
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from tools.remediate_lens_oct27_28_query_gaps import (
    LENS_OCT27_28_CANONICAL_SOURCES,
    MICHAEL_REISS_NODE_ID,
    remediate_lens_oct27_28_query_gaps,
)


class TestRemediateLensOct2728QueryGaps(unittest.TestCase):
    """Test Wave 3 governed promotion of October 27-28 LENS operational assertions in an isolated sandbox runtime."""

    def _provision_isolated_runtime_from_active(self, tmp_dir: Path) -> Path:
        auth_rt = resolve_authoritative_runtime_root(ROOT)
        active_pair_path = auth_rt / "active_pair.json"
        if not active_pair_path.is_file():
            self.skipTest("Authoritative runtime active_pair.json is not present")
        active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
        gen_id = active_pair["generation_id"]
        src_graph = (auth_rt / active_pair["graph_path"]).resolve()
        src_sidecar = (auth_rt / active_pair["sidecar_path"]).resolve()
        if not src_graph.is_file() or not src_sidecar.is_file():
            self.skipTest("Active generation graph/sidecar files are not present")

        sandbox_rt = tmp_dir / "runtime"
        gen_dir = sandbox_rt / "generations" / gen_id
        gen_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_graph, gen_dir / "graph.sqlite")
        shutil.copy2(src_sidecar, gen_dir / "sidecar.sqlite")
        (sandbox_rt / "active_pair.json").write_text(
            json.dumps(active_pair, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return sandbox_rt

    def test_governed_lens_oct27_28_promotion_and_5_check_physical_proof(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wave3-lens-sandbox-") as tmpdir:
            tmp_path = Path(tmpdir)
            sandbox_rt = self._provision_isolated_runtime_from_active(tmp_path)
            receipt_file = tmp_path / "wave3_lens_promotion_sandbox_receipt.json"

            receipt = remediate_lens_oct27_28_query_gaps(
                runtime_root=sandbox_rt,
                receipt_path=receipt_file,
            )
            self.assertEqual("PASS", receipt["terminal_outcome"])
            self.assertEqual("PASS", receipt["physical_proof"]["overall"])
            checks = {c["check"]: c["status"] for c in receipt["physical_proof"]["checks"]}
            for required_check in ("file_changed", "raw_bytes", "integrity", "readback", "row_delta"):
                self.assertEqual("PASS", checks.get(required_check), f"Failed check: {required_check}")

            # Independent read-only verification on the promoted generation
            promoted_pair = json.loads((sandbox_rt / "active_pair.json").read_text(encoding="utf-8"))
            new_graph_path = sandbox_rt / promoted_pair["graph_path"]
            new_sidecar_path = sandbox_rt / promoted_pair["sidecar_path"]
            self.assertEqual(promoted_pair["graph_sha256"], sha256_file(new_graph_path))
            self.assertEqual(promoted_pair["sidecar_sha256"], sha256_file(new_sidecar_path))

            # Validate sidecar binding
            sidecar_val = validate_sidecar(new_sidecar_path)
            self.assertEqual(promoted_pair["graph_sha256"], sidecar_val["metadata"]["graph_sha256"])

            ro_conn = sqlite3.connect(f"file:{new_graph_path}?mode=ro", uri=True)
            try:
                oct28_cnt = ro_conn.execute(
                    """SELECT COUNT(*) FROM assertion
                        WHERE primary_source_id = 'source-ed01b486836314055f69c725'
                          AND canonical_status = 'canonical'
                          AND canonical_eligible = 1
                          AND retrieval_eligible = 1"""
                ).fetchone()[0]
                self.assertEqual(39, oct28_cnt)

                for sid, expected_min in (
                    ("source-d4d6a747fa66307138d4f96a", 49),
                    ("source-e17a00483f549a6495c38d44", 41),
                ):
                    cnt = ro_conn.execute(
                        """SELECT COUNT(*) FROM assertion
                            WHERE primary_source_id = ?
                              AND canonical_status = 'canonical'
                              AND canonical_eligible = 1
                              AND retrieval_eligible = 1""",
                        (sid,),
                    ).fetchone()[0]
                    self.assertEqual(expected_min, cnt)

                # Verify Michael Rice alias is registered on Michael Reiss node
                alias_row = ro_conn.execute(
                    "SELECT entity_id, display_alias FROM canonical_alias WHERE entity_id = ? AND lower(display_alias) = 'michael rice' AND active = 1",
                    (MICHAEL_REISS_NODE_ID,),
                ).fetchone()
                self.assertIsNotNone(alias_row)
            finally:
                ro_conn.close()

            # End-to-end query verification over promoted graph
            engine = GraphFirstQueryEngine(ROOT, new_graph_path, new_sidecar_path)
            q_res = engine.execute_query("What are the open steps and latest meeting gates to complete LENS SOW 1?")
            self.assertTrue(q_res.success)
            self.assertEqual(1, q_res.resolved_tier)
            all_claims_text = " ".join(
                f"{a.get('subject_name', '')} {a.get('object_literal', '')} {a.get('object_name', '')}"
                for a in q_res.assertions
            ).lower()
            for expected_phrase in (
                "turbine",
                "odyssey",
                "200",
                "reiss",
                "2,500",
                "messaging queue",
            ):
                self.assertIn(expected_phrase, all_claims_text)


if __name__ == "__main__":
    unittest.main()
