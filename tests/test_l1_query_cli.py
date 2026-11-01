"""Wave 7 CLI keeps bounded mode explicit and candidate-only."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli import _l1_control_policy_from_args, build_parser

ROOT = Path(__file__).resolve().parents[1]


class L1QueryCliTests(unittest.TestCase):
    def test_parser_exposes_closed_controller_modes_and_policy_path(self) -> None:
        args = build_parser().parse_args(["ask", "--question", "test", "--l1-controller-mode", "bounded"])
        self.assertEqual("bounded", args.l1_controller_mode)
        self.assertIsNone(args.l1_controller_policy)
        with self.assertRaisesRegex(ValueError, "L1_BOUNDED_POLICY_REQUIRED"):
            _l1_control_policy_from_args(args)

    def test_bounded_mode_requires_a_bounded_policy_file(self) -> None:
        value = json.loads((ROOT / "requirements" / "query-l1-control-policy.json").read_text(encoding="utf-8"))
        value["mode"] = "bounded"
        with tempfile.TemporaryDirectory() as temp:
            policy_path = Path(temp) / "candidate.json"
            policy_path.write_text(json.dumps(value), encoding="utf-8")
            args = build_parser().parse_args([
                "ask", "--question", "test", "--l1-controller-mode", "bounded",
                "--l1-controller-policy", str(policy_path),
            ])
            policy = _l1_control_policy_from_args(args)
            self.assertEqual("bounded", policy.mode)


if __name__ == "__main__":
    unittest.main()
