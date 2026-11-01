"""Boundary, malformed input, invariant bypass, stale state, and rollback cases."""
import shutil
from unittest.mock import patch

from dual_pipeline.core import read_json
from dual_pipeline.graph_consolidation_core.apply import apply_consolidation_plan
from dual_pipeline.graph_consolidation_core.release import prepare_consolidation_release
from dual_pipeline.storage.governed_context import GovernedStorageContext
from tests.redirect_repair_fixtures import RedirectFixture


class RedirectRepairAdversarialTests(RedirectFixture):
    def rejected(self):
        before = self.rows(self.staged, "entity_redirect")
        with self.assertRaises((ValueError, RuntimeError, PermissionError, FileNotFoundError)):
            self.apply()
        self.assertEqual(before, self.rows(self.staged, "entity_redirect"))
        self.assertEqual([], self.rows(self.staged, "audit_event"))

    def test_empty_plan_rejected(self):
        self.plan["operations"] = self.plan["proposed_operations"] = []
        self.reseal()
        self.rejected()

    def test_disagreeing_operation_lists(self):
        self.plan["proposed_operations"] = []
        self.reseal()
        self.rejected()

    def test_mixed_operation_types(self):
        self.plan["operations"][1]["operation_type"] = "merge_node"
        self.reseal()
        self.rejected()

    def test_duplicate_origin(self):
        self.plan["operations"][1]["from_node_id"] = "a"
        self.reseal()
        self.rejected()

    def test_removed_proposal_discriminator(self):
        self.plan.pop("proposal_type")
        self.reseal()
        self.rejected()

    def test_extra_operation_fields(self):
        self.plan["operations"][0]["bypass"] = True
        self.reseal()
        self.rejected()

    def test_scope_change_cannot_reuse_approval(self):
        self.plan["operations"][0]["new_target"] = "f"
        self.reseal()
        self.rejected()

    def test_unrecorded_approval(self):
        self.approval = "human-sounding-but-unrecorded"
        self.rejected()

    def test_code_only_approval(self):
        self.approval = self.approve(approved_waves=[1], graph_mutation_allowed=False)
        self.rejected()

    def test_synthetic_approval(self):
        self.approval = self.approve(user_message="Automatically approved the artifact")
        self.rejected()

    def test_tampered_ledger(self):
        with GovernedStorageContext.write_context(self.root.parent / "ledger/hitl_decision_ledger.sqlite") as conn:
            conn.execute("UPDATE hitl_decisions SET rationale='tampered' WHERE entry_id=?", (self.approval,))
        self.rejected()

    def test_stale_pointer(self):
        self.pointer.write_text(self.pointer.read_text() + " ")
        self.rejected()

    def test_stale_graph(self):
        with GovernedStorageContext.write_context(self.graph) as conn:
            conn.execute("UPDATE assertion SET value='new fact'")
        self.rejected()

    def test_stale_sidecar(self):
        with GovernedStorageContext.write_context(self.sidecar) as conn:
            conn.execute("INSERT INTO metadata VALUES ('new','value')")
        self.rejected()

    def test_missing_backup(self):
        (self.backup / "graph.sqlite").unlink()
        self.rejected()

    def test_tampered_backup(self):
        with GovernedStorageContext.write_context(self.backup / "graph.sqlite") as conn:
            conn.execute("DELETE FROM assertion")
        self.rejected()

    def test_direct_live_write_rejected(self):
        self.staged = self.graph
        self.rejected()

    def test_symlink_live_write_rejected(self):
        self.staged.unlink()
        self.staged.symlink_to(self.graph)
        self.rejected()

    def test_hardlink_live_write_rejected(self):
        self.staged.unlink()
        self.staged.hardlink_to(self.graph)
        self.rejected()

    def test_hardlink_backup_write_rejected(self):
        self.staged.unlink()
        self.staged.hardlink_to(self.backup / "graph.sqlite")
        self.rejected()

    def test_apply_without_runtime_rejected(self):
        with self.assertRaises(PermissionError):
            apply_consolidation_plan(self.staged, plan=self.plan, approval_id=self.approval, run_id="test")

    def test_incorrect_second_target_leaves_first_untouched(self):
        self.plan["operations"][1]["new_target"] = "f"
        self.reseal()
        self.approval = self.approve()
        self.rejected()

    def rebuild(self):
        self.refresh_baseline()
        self.plan = self.build()
        self.approval = self.approve()
        shutil.copyfile(self.graph, self.staged)

    def test_trigger_out_of_scope_write_rolls_back(self):
        with GovernedStorageContext.write_context(self.graph) as conn:
            conn.execute("CREATE TRIGGER extra_write AFTER UPDATE ON entity_redirect "
                         "BEGIN UPDATE assertion SET value='unauthorized'; END")
        self.rebuild()
        self.rejected()
        self.assertEqual(self.rows(self.graph, "assertion"), self.rows(self.staged, "assertion"))

    def test_failure_after_first_update_rolls_back(self):
        with GovernedStorageContext.write_context(self.graph) as conn:
            conn.execute("CREATE TRIGGER fail_second BEFORE UPDATE ON entity_redirect "
                         "WHEN OLD.from_node_id='d' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.rebuild()
        from dual_pipeline.storage.governed_context import IntegrityError
        with self.assertRaises(IntegrityError):
            self.apply()
        self.assertEqual(self.rows(self.graph, "entity_redirect"), self.rows(self.staged, "entity_redirect"))
        self.assertEqual([], self.rows(self.staged, "audit_event"))

    def test_cycles_rejected_by_preview(self):
        with GovernedStorageContext.write_context(self.graph) as conn:
            conn.execute("INSERT INTO entity_redirect VALUES ('c','a','merge-original',1,'original-date')")
        self.refresh_baseline()
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.build()

    def test_missing_target_rejected_by_preview(self):
        with GovernedStorageContext.write_context(self.graph) as conn:
            conn.execute("DELETE FROM node WHERE node_id='f'")
        self.changes[0]["new_target"] = "f"
        self.refresh_baseline()
        with self.assertRaises(ValueError):
            self.build()

    def test_single_hop_is_not_a_repair(self):
        self.changes = [dict(from_node_id="b", old_target="c", new_target="c")]
        with self.assertRaises(ValueError):
            self.build()

    def test_drift_during_transaction_rolls_back(self):
        from dual_pipeline.graph_consolidation_core.redirect_repair import apply_redirect
        def drifting(*args, **kwargs):
            receipt = apply_redirect(*args, **kwargs)
            self.pointer.write_text(self.pointer.read_text() + " ")
            return receipt
        with patch("dual_pipeline.graph_consolidation_core.apply.apply_redirect", side_effect=drifting):
            self.rejected()

    def test_prepare_rejects_code_only_before_creating_generation(self):
        self.approval = self.approve(approved_waves=[1], graph_mutation_allowed=False)
        before = sorted((self.root / "generations").iterdir())
        with self.assertRaises(PermissionError):
            prepare_consolidation_release(manager=self.manager(), backend=self.backend(),
                plan=self.plan, run_id="fixture-release", approval_id=self.approval)
        self.assertEqual(before, sorted((self.root / "generations").iterdir()))
        self.assertEqual(read_json(self.pointer), self.active)
