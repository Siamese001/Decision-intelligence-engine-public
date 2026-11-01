#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "nee_pre_mutation_gate.py"
SPEC = importlib.util.spec_from_file_location("nee_gate", TOOL_PATH)
assert SPEC and SPEC.loader
nee_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nee_gate)


class NeeGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nee_gate_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def write_json(self, name: str, value: dict) -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def proposal(self, proposal_id: str) -> dict:
        value = {
            "proposal_id": proposal_id,
            "proposal_type": "test",
            "status": "proposed",
            "operations": [],
        }
        value["canonical_serialization_sha256"] = nee_gate.proposal_digest(value)
        return value

    def metric_rows(self) -> list[dict]:
        return [
            {
                "metric": metric,
                "result": "0 → 0 (+0)",
                "pass_threshold": threshold,
                "status": "PASS",
                "comment": "No regression; control remains stable.",
            }
            for metric, threshold in nee_gate.METRIC_SPECS
        ]

    def graph_rows(self) -> list[dict]:
        specs = [
            ("source", "Sources", "10", "11", "+1"),
            ("node", "Nodes", "20", "21", "+1"),
            ("alias", "Aliases", "5", "5", "+0"),
            ("edge", "Edges — total / active", "30 / 29", "31 / 30", "+1 / +1"),
            ("claim", "Claims — total / active", "40 / 38", "41 / 39", "+1 / +1"),
            ("validation", "Validation items", "4", "5", "+1"),
            ("node_card_map", "Node-to-card mappings", "8", "9", "+1"),
        ]
        return [
            {
                "component_key": key,
                "graph_component": label,
                "baseline": before,
                "proposed_or_actual": after,
                "delta": delta,
                "change": f"Controlled {label.lower()} update.",
                "retrieval_rationale": "Adds governed exact-match, lineage, or routing evidence without changing unsupported authority.",
            }
            for key, label, before, after, delta in specs
        ]

    def inline_graph_proposal(self) -> dict:
        rows = self.graph_rows()
        value = {
            "proposal_id": "G1-inline",
            "proposal_type": "proposed_graph_delta",
            "status": "proposed",
            "component_counts": {
                row["component_key"]: {
                    "before": row["baseline"],
                    "after": row["proposed_or_actual"],
                    "delta": row["delta"],
                }
                for row in rows
            },
            "operations": [
                {
                    "object_type": "node_card_map",
                    "card_routes": ["NEE_01"],
                    "proposed_after": {"card_id": "NEE_01"},
                }
            ],
            "inline_gate_output": {
                "graph_delta_rows": rows,
                "graph_metric_rows": self.metric_rows(),
            },
        }
        value["canonical_serialization_sha256"] = nee_gate.proposal_digest(value)
        return value

    def inline_card_proposal(self) -> dict:
        value = {
            "proposal_id": "C1-inline",
            "proposal_type": "proposed_card_delta",
            "status": "proposed",
            "cards": [{"card_id": "NEE_01"}],
            "unlisted_cards_unchanged": True,
            "inline_gate_output": {
                "card_rows": [
                    {
                        "card": "NEE_01",
                        "addition": "Add the approved source route.",
                        "change_type": "route",
                        "defense_or_evidence": "The route is present in the approved graph proposal.",
                        "retrieval_improvement": "Allows strategy queries to traverse from the card to the governed source claim.",
                    }
                ]
            },
        }
        value["canonical_serialization_sha256"] = nee_gate.proposal_digest(value)
        return value

    def test_proposal_digest_ignores_declared_digest_field(self) -> None:
        value = self.proposal("G1-test")
        first = nee_gate.proposal_digest(value)
        value["canonical_serialization_sha256"] = "0" * 64
        second = nee_gate.proposal_digest(value)
        self.assertEqual(first, second)

    def test_missing_approval_fails_closed(self) -> None:
        baseline = self.tmp / "baseline.zip"
        baseline.write_bytes(b"baseline")
        graph = self.write_json("graph.json", self.proposal("G1"))
        card = self.write_json("card.json", self.proposal("C1"))
        with self.assertRaises(nee_gate.GateError):
            nee_gate.validate_approval(
                self.tmp / "missing.json", baseline, graph, card
            )

    def test_mismatched_baseline_fails(self) -> None:
        baseline = self.tmp / "baseline.zip"
        baseline.write_bytes(b"baseline")
        graph_obj = self.proposal("G1")
        card_obj = self.proposal("C1")
        graph = self.write_json("graph.json", graph_obj)
        card = self.write_json("card.json", card_obj)
        approval = {
            "decision_id": "D",
            "status": "approved",
            "approval_precedes_implementation": True,
            "baseline_zip": {"sha256": "f" * 64},
            "graph_proposal": {
                "canonical_serialization_sha256": graph_obj["canonical_serialization_sha256"]
            },
            "card_proposal": {
                "canonical_serialization_sha256": card_obj["canonical_serialization_sha256"]
            },
        }
        approval_path = self.write_json("approval.json", approval)
        with self.assertRaises(nee_gate.GateError):
            nee_gate.validate_approval(
                approval_path, baseline, graph, card
            )

    def test_valid_approval_passes(self) -> None:
        baseline = self.tmp / "baseline.zip"
        baseline.write_bytes(b"baseline")
        graph_obj = self.proposal("G1")
        card_obj = self.proposal("C1")
        graph = self.write_json("graph.json", graph_obj)
        card = self.write_json("card.json", card_obj)
        approval = {
            "decision_id": "D",
            "status": "approved",
            "approval_precedes_implementation": True,
            "baseline_zip": {"sha256": nee_gate.sha256_file(baseline)},
            "graph_proposal": {
                "canonical_serialization_sha256": graph_obj["canonical_serialization_sha256"]
            },
            "card_proposal": {
                "canonical_serialization_sha256": card_obj["canonical_serialization_sha256"]
            },
        }
        approval_path = self.write_json("approval.json", approval)
        result = nee_gate.validate_approval(
            approval_path, baseline, graph, card
        )
        self.assertEqual(result["status"], "pass")

    def test_unapproved_file_change_fails(self) -> None:
        baseline_dir = self.tmp / "baseline"
        work_dir = self.tmp / "work"
        baseline_dir.mkdir()
        work_dir.mkdir()
        (baseline_dir / "a.txt").write_text("same", encoding="utf-8")
        (work_dir / "a.txt").write_text("changed", encoding="utf-8")
        approval = {
            "status": "approved",
            "approved_scope": {
                "approved_file_paths": [],
                "account_graph_delta": "not_zero",
                "non_policy_card_delta": "not_zero",
            },
        }
        approval_path = self.write_json("approval.json", approval)
        with self.assertRaises(nee_gate.GateError):
            nee_gate.verify_scope(
                approval_path, baseline_dir, work_dir
            )

    def test_package_requires_pass_conformance(self) -> None:
        source = self.tmp / "NEE_vX"
        source.mkdir()
        (source / "README.md").write_text("x", encoding="utf-8")
        baseline = self.tmp / "baseline.zip"
        baseline.write_bytes(b"baseline")
        graph_obj = self.proposal("G1")
        card_obj = self.proposal("C1")
        graph = self.write_json("graph.json", graph_obj)
        card = self.write_json("card.json", card_obj)
        approval = {
            "decision_id": "D",
            "status": "approved",
            "approval_precedes_implementation": True,
            "baseline_zip": {"sha256": nee_gate.sha256_file(baseline)},
            "graph_proposal": {
                "canonical_serialization_sha256": graph_obj["canonical_serialization_sha256"]
            },
            "card_proposal": {
                "canonical_serialization_sha256": card_obj["canonical_serialization_sha256"]
            },
        }
        approval_path = self.write_json("approval.json", approval)
        conformance_path = self.write_json(
            "conformance.json",
            {
                "approval_decision_id": "D",
                "status": "fail",
                "unapproved_semantic_changes": [],
                "drift_result": "none",
                "packaging_gate_result": "fail",
            },
        )
        with self.assertRaises(nee_gate.GateError):
            nee_gate.gated_package(
                approval_path,
                baseline,
                self.tmp,
                graph,
                card,
                conformance_path,
                source,
                self.tmp / "out.zip",
            )

    def test_exact_three_table_inline_output_passes(self) -> None:
        graph = self.inline_graph_proposal()
        card = self.inline_card_proposal()
        output = self.tmp / "inline.md"
        output.write_text(
            nee_gate.render_inline_output("G1", graph_proposal=graph, card_proposal=card),
            encoding="utf-8",
        )
        result = nee_gate.validate_inline_output_file(
            output,
            "G1",
            graph_proposal=graph,
            card_proposal=card,
        )
        self.assertEqual(result["table_count"], 3)
        self.assertEqual(result["metric_rows"], 10)

    def test_extra_inline_narrative_fails(self) -> None:
        graph = self.inline_graph_proposal()
        card = self.inline_card_proposal()
        output = self.tmp / "inline.md"
        output.write_text(
            "Summary\n\n" + nee_gate.render_inline_output("G1", graph_proposal=graph, card_proposal=card),
            encoding="utf-8",
        )
        with self.assertRaises(nee_gate.GateError):
            nee_gate.validate_inline_output_file(
                output,
                "G1",
                graph_proposal=graph,
                card_proposal=card,
            )

    def test_missing_metric_row_fails(self) -> None:
        graph = self.inline_graph_proposal()
        graph["inline_gate_output"]["graph_metric_rows"].pop()
        card = self.inline_card_proposal()
        with self.assertRaises(nee_gate.GateError):
            nee_gate.render_inline_output("G1", graph_proposal=graph, card_proposal=card)

    def test_failed_metric_blocks(self) -> None:
        graph = self.inline_graph_proposal()
        graph["inline_gate_output"]["graph_metric_rows"][0]["status"] = "FAIL"
        graph["inline_gate_output"]["graph_metric_rows"][0]["comment"] = "Orphan regression requires repair."
        card = self.inline_card_proposal()
        with self.assertRaises(nee_gate.GateError):
            nee_gate.render_inline_output("G1", graph_proposal=graph, card_proposal=card)

    def test_graph_delta_arithmetic_mismatch_fails(self) -> None:
        graph = self.inline_graph_proposal()
        graph["inline_gate_output"]["graph_delta_rows"][0]["delta"] = "+2"
        graph["component_counts"]["source"]["delta"] = "+2"
        card = self.inline_card_proposal()
        with self.assertRaises(nee_gate.GateError):
            nee_gate.render_inline_output("G1", graph_proposal=graph, card_proposal=card)

    def test_missing_affected_card_fails(self) -> None:
        graph = self.inline_graph_proposal()
        card = self.inline_card_proposal()
        card["inline_gate_output"]["card_rows"][0]["card"] = "NEE_02"
        with self.assertRaises(nee_gate.GateError):
            nee_gate.render_inline_output("G1", graph_proposal=graph, card_proposal=card)

    def test_zero_scalar_delta_renders_na(self) -> None:
        graph = self.inline_graph_proposal()
        card = self.inline_card_proposal()
        rendered = nee_gate.render_inline_output(
            "G1", graph_proposal=graph, card_proposal=card
        )
        self.assertIn(
            "| Aliases | 5 | 5 | +0 | Controlled aliases update. | NA |",
            rendered,
        )

    def test_zero_vector_delta_renders_na(self) -> None:
        graph = self.inline_graph_proposal()
        edge_row = graph["inline_gate_output"]["graph_delta_rows"][3]
        edge_row["proposed_or_actual"] = edge_row["baseline"]
        edge_row["delta"] = "+0 / +0"
        graph["component_counts"]["edge"]["after"] = edge_row["baseline"]
        graph["component_counts"]["edge"]["delta"] = "+0 / +0"
        card = self.inline_card_proposal()
        rendered = nee_gate.render_inline_output(
            "G1", graph_proposal=graph, card_proposal=card
        )
        self.assertIn(
            "| Edges — total / active | 30 / 29 | 30 / 29 | +0 / +0 | Controlled edges — total / active update. | NA |",
            rendered,
        )

    def test_nonzero_delta_na_fails(self) -> None:
        graph = self.inline_graph_proposal()
        graph["inline_gate_output"]["graph_delta_rows"][0]["retrieval_rationale"] = "NA"
        card = self.inline_card_proposal()
        with self.assertRaises(nee_gate.GateError):
            nee_gate.render_inline_output(
                "G1", graph_proposal=graph, card_proposal=card
            )

    def test_nonzero_vector_delta_specific_rationale_passes(self) -> None:
        graph = self.inline_graph_proposal()
        card = self.inline_card_proposal()
        rendered = nee_gate.render_inline_output(
            "G1", graph_proposal=graph, card_proposal=card
        )
        self.assertIn(
            "| Edges — total / active | 30 / 29 | 31 / 30 | +1 / +1 | Controlled edges — total / active update. | Adds governed exact-match, lineage, or routing evidence without changing unsupported authority. |",
            rendered,
        )

    def test_all_zero_graph_renders_na_for_every_rationale(self) -> None:
        graph = self.inline_graph_proposal()
        rows = graph["inline_gate_output"]["graph_delta_rows"]
        for row in rows:
            row["proposed_or_actual"] = row["baseline"]
            row["delta"] = "+0 / +0" if "/" in row["baseline"] else "+0"
            row["retrieval_rationale"] = "Verbose text that must not render."
            graph["component_counts"][row["component_key"]]["after"] = row["baseline"]
            graph["component_counts"][row["component_key"]]["delta"] = row["delta"]
        card = self.inline_card_proposal()
        rendered = nee_gate.render_inline_output(
            "G1", graph_proposal=graph, card_proposal=card
        )
        graph_lines = [
            line
            for line in rendered.splitlines()
            if line.startswith("| ") and not line.startswith("| Graph component")
        ][: len(rows)]
        self.assertEqual(len(graph_lines), len(rows))
        self.assertTrue(all(line.endswith("| NA |") for line in graph_lines))

    def test_inline_output_is_approval_bound(self) -> None:
        baseline = self.tmp / "baseline.zip"
        baseline.write_bytes(b"baseline")
        graph_obj = self.inline_graph_proposal()
        card_obj = self.inline_card_proposal()
        graph = self.write_json("graph.json", graph_obj)
        card = self.write_json("card.json", card_obj)
        inline = self.tmp / "g1_inline.md"
        inline.write_text(
            nee_gate.render_inline_output("G1", graph_proposal=graph_obj, card_proposal=card_obj),
            encoding="utf-8",
        )
        approval = {
            "decision_id": "D-inline",
            "status": "approved",
            "approval_precedes_implementation": True,
            "inline_output_gate_required": True,
            "g1_inline_output_sha256": nee_gate.sha256_file(inline),
            "baseline_zip": {"sha256": nee_gate.sha256_file(baseline)},
            "graph_proposal": {
                "canonical_serialization_sha256": graph_obj["canonical_serialization_sha256"]
            },
            "card_proposal": {
                "canonical_serialization_sha256": card_obj["canonical_serialization_sha256"]
            },
        }
        approval_path = self.write_json("approval_inline.json", approval)
        result = nee_gate.validate_approval(
            approval_path, baseline, graph, card, inline
        )
        self.assertEqual(result["g1_inline_output_sha256"], nee_gate.sha256_file(inline))

    def test_inline_gate_required_without_file_fails(self) -> None:
        baseline = self.tmp / "baseline.zip"
        baseline.write_bytes(b"baseline")
        graph_obj = self.inline_graph_proposal()
        card_obj = self.inline_card_proposal()
        graph = self.write_json("graph.json", graph_obj)
        card = self.write_json("card.json", card_obj)
        approval = {
            "decision_id": "D-inline",
            "status": "approved",
            "approval_precedes_implementation": True,
            "inline_output_gate_required": True,
            "g1_inline_output_sha256": "0" * 64,
            "baseline_zip": {"sha256": nee_gate.sha256_file(baseline)},
            "graph_proposal": {
                "canonical_serialization_sha256": graph_obj["canonical_serialization_sha256"]
            },
            "card_proposal": {
                "canonical_serialization_sha256": card_obj["canonical_serialization_sha256"]
            },
        }
        approval_path = self.write_json("approval_inline_missing.json", approval)
        with self.assertRaises(nee_gate.GateError):
            nee_gate.validate_approval(
                approval_path, baseline, graph, card
            )

    def test_synthetic_approval_origin_fails(self) -> None:
        baseline = self.tmp / "baseline.zip"
        baseline.write_bytes(b"baseline")
        graph_obj = self.proposal("G1")
        card_obj = self.proposal("C1")
        graph = self.write_json("graph.json", graph_obj)
        card = self.write_json("card.json", card_obj)
        approval = {
            "decision_id": "D-synth",
            "status": "approved",
            "operator_origin": "system_message auto-approved",
            "approval_precedes_implementation": True,
            "baseline_zip": {"sha256": nee_gate.sha256_file(baseline)},
            "graph_proposal": {
                "canonical_serialization_sha256": graph_obj["canonical_serialization_sha256"]
            },
            "card_proposal": {
                "canonical_serialization_sha256": card_obj["canonical_serialization_sha256"]
            },
        }
        approval_path = self.write_json("approval_synth.json", approval)
        with self.assertRaises(nee_gate.GateError) as ctx:
            nee_gate.validate_approval(approval_path, baseline, graph, card)
        self.assertIn("Approval origin rejected", str(ctx.exception))

    def test_stale_base_generation_fails(self) -> None:
        baseline = self.tmp / "baseline.zip"
        baseline.write_bytes(b"baseline")
        graph_obj = self.proposal("G1")
        card_obj = self.proposal("C1")
        graph = self.write_json("graph.json", graph_obj)
        card = self.write_json("card.json", card_obj)
        approval = {
            "decision_id": "D-stale",
            "status": "approved",
            "operator_origin": "USER_INPUT",
            "expected_base_generation": "generation-100",
            "actual_base_generation": "generation-101",
            "approval_precedes_implementation": True,
            "baseline_zip": {"sha256": nee_gate.sha256_file(baseline)},
            "graph_proposal": {
                "canonical_serialization_sha256": graph_obj["canonical_serialization_sha256"]
            },
            "card_proposal": {
                "canonical_serialization_sha256": card_obj["canonical_serialization_sha256"]
            },
        }
        approval_path = self.write_json("approval_stale.json", approval)
        with self.assertRaises(nee_gate.GateError) as ctx:
            nee_gate.validate_approval(approval_path, baseline, graph, card)
        self.assertIn("Stale base generation", str(ctx.exception))

    def test_valid_human_origin_passes(self) -> None:
        baseline = self.tmp / "baseline.zip"
        baseline.write_bytes(b"baseline")
        graph_obj = self.proposal("G1")
        card_obj = self.proposal("C1")
        graph = self.write_json("graph.json", graph_obj)
        card = self.write_json("card.json", card_obj)
        approval = {
            "decision_id": "D-valid-human",
            "status": "approved",
            "operator_origin": "USER_INPUT",
            "expected_base_generation": "generation-100",
            "actual_base_generation": "generation-100",
            "approval_precedes_implementation": True,
            "baseline_zip": {"sha256": nee_gate.sha256_file(baseline)},
            "graph_proposal": {
                "canonical_serialization_sha256": graph_obj["canonical_serialization_sha256"]
            },
            "card_proposal": {
                "canonical_serialization_sha256": card_obj["canonical_serialization_sha256"]
            },
        }
        approval_path = self.write_json("approval_valid_human.json", approval)
        result = nee_gate.validate_approval(approval_path, baseline, graph, card)
        self.assertEqual(result["status"], "pass")


if __name__ == "__main__":
    unittest.main(verbosity=2)
