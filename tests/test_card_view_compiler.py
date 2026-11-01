import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.card_views import (
    export_all_card_views,
    materialize_card_view,
    render_card_markdown,
)
from dual_pipeline.core import read_json
from tools.cards_refactor import _parser, execute

ROOT = Path(__file__).resolve().parent.parent


class TestCardViewCompiler(unittest.TestCase):
    def setUp(self):
        self.runtime_root = ROOT / ".local" / "dual_pipeline"
        self.active_pair = read_json(self.runtime_root / "active_pair.json")
        self.graph_path = self.runtime_root / self.active_pair["graph_path"]
        self.con = sqlite3.connect(f"file:{self.graph_path.as_posix()}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row

    def tearDown(self):
        self.con.close()

    def test_render_card_markdown_structure(self):
        receipt = materialize_card_view(
            self.con,
            card_id="NEE_01",
            generation_id=self.active_pair["generation_id"],
            graph_sha256=self.active_pair["graph_sha256"],
            authorization_context={
                "audiences": ["internal_consulting"],
                "classification_clearance": "internal",
                "candidate_review": False,
            },
        )
        md = render_card_markdown(self.con, receipt)
        self.assertIn("card_id: NEE_01", md)
        self.assertIn("generation_id:", md)
        self.assertIn("card_content_used_as_evidence: false", md)
        self.assertIn("# NEE_01", md)
        self.assertIn("## Epistemic Provenance & Cryptographic Envelope", md)

    def test_export_all_card_views(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exported = export_all_card_views(
                runtime_root=self.runtime_root,
                output_dir=Path(tmpdir),
                authorization_context={
                    "audiences": ["internal_consulting"],
                    "classification_clearance": "internal",
                    "candidate_review": False,
                },
            )
            self.assertEqual(len(exported), 19)
            card_01 = Path(tmpdir) / "NEE_01.md"
            self.assertTrue(card_01.is_file())
            content = card_01.read_text(encoding="utf-8")
            self.assertIn("card_id: NEE_01", content)

    def test_cli_compile_and_compile_all(self):
        parser = _parser()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "test_out.md"
            args = parser.parse_args([
                "--runtime-root", str(self.runtime_root),
                "compile",
                "--card-id", "NEE_01",
                "--output", str(out_file),
            ])
            res = execute(args)
            self.assertEqual(res["status"], "PASS")
            self.assertTrue(out_file.is_file())

            out_dir = Path(tmpdir) / "all_cards"
            args_all = parser.parse_args([
                "--runtime-root", str(self.runtime_root),
                "compile-all",
                "--output-dir", str(out_dir),
            ])
            res_all = execute(args_all)
            self.assertEqual(res_all["status"], "PASS")
            self.assertEqual(res_all["exported_count"], 19)


if __name__ == "__main__":
    unittest.main()
