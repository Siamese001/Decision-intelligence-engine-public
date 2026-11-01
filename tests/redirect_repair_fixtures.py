"""Disposable fixtures; never use the repository runtime or any embedding model."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from dual_pipeline.core import digest, read_json, sha256_file
from dual_pipeline.graph_consolidation_core.apply import apply_consolidation_plan
from dual_pipeline.graph_consolidation_core.redirect_repair import build_redirect_repair_plan
from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.storage.hitl_sqlite_store import HITLSQLiteStore

SCHEMA = """
CREATE TABLE node(node_id TEXT PRIMARY KEY, active INTEGER);
CREATE TABLE assertion(assertion_id TEXT PRIMARY KEY, value TEXT);
CREATE TABLE entity_merge_event(merge_event_id TEXT PRIMARY KEY);
CREATE TABLE entity_redirect(
 from_node_id TEXT PRIMARY KEY REFERENCES node(node_id),
 to_node_id TEXT REFERENCES node(node_id),
 merge_event_id TEXT REFERENCES entity_merge_event(merge_event_id),
 active INTEGER, created_at TEXT);
CREATE TABLE audit_event(
 audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT, action TEXT,
 record_type TEXT, record_id TEXT, previous_value_json TEXT, new_value_json TEXT,
 source_id TEXT, event_timestamp TEXT, reason TEXT, validation_reference TEXT,
 approval_reference TEXT, migration_or_ingestion_run_id TEXT, release_version TEXT);
"""


class RedirectFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="redirect-repair-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / ".local/dual_pipeline"
        self.root.mkdir(parents=True)
        self.graph = self.root / "generations/baseline/graph.sqlite"
        self.graph.parent.mkdir(parents=True)
        with GovernedStorageContext.write_context(self.graph) as conn:
            conn.executescript(SCHEMA)
            conn.executemany("INSERT INTO node VALUES (?,1)", [(x,) for x in "abcdef"])
            conn.execute("INSERT INTO entity_merge_event VALUES ('merge-original')")
            conn.executemany("INSERT INTO entity_redirect VALUES (?,?, 'merge-original',1,'original-date')",
                             [("a", "b"), ("b", "c"), ("d", "a"), ("e", "b")])
            conn.execute("INSERT INTO assertion VALUES ('fact', 'preserve exactly')")
        self.sidecar = self.graph.parent / "sidecar.sqlite"
        with GovernedStorageContext.write_context(self.sidecar) as conn:
            conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        self.active = {
            "generation_id": "baseline", "graph_path": str(self.graph.relative_to(self.root)),
            "sidecar_path": str(self.sidecar.relative_to(self.root)),
            "graph_sha256": sha256_file(self.graph), "sidecar_sha256": sha256_file(self.sidecar),
            "embedding_backend": "fixture-only", "production_qualified": False,
            "model_id": "fixture-only", "model_revision": "fixture-only",
            "model_manifest_digest": "fixture-only", "graph_snapshot": "fixture-only",
        }
        self.pointer = self.root / "active_pair.json"
        self.pointer.write_text(json.dumps(self.active))
        self.backup = self.root.parent / "hygiene_backups/test"
        self.backup.mkdir(parents=True)
        self.manifest = self.backup / "manifest.json"
        self.refresh_baseline()
        self.changes = [dict(from_node_id=x, old_target=y, new_target="c")
                        for x, y in [("a", "b"), ("d", "a"), ("e", "b")]]
        self.plan = self.build()
        self.approval = self.approve()
        self.staged = self.root / "generations/candidate/graph.sqlite"
        self.staged.parent.mkdir()
        shutil.copyfile(self.graph, self.staged)

    def refresh_baseline(self):
        self.active["graph_sha256"] = sha256_file(self.graph)
        self.active["sidecar_sha256"] = sha256_file(self.sidecar)
        self.pointer.write_text(json.dumps(self.active))
        files = []
        for source in (self.graph, self.sidecar, self.pointer):
            backup = self.backup / source.name
            shutil.copyfile(source, backup)
            files.append(dict(source=str(source), backup=str(backup), sha256=sha256_file(source)))
        self.manifest.write_text(json.dumps(dict(generation_id="baseline", files=files)))

    def build(self):
        return build_redirect_repair_plan(self.root, self.changes, self.manifest)

    def approve(self, **overrides):
        metadata = dict(source="user_input", operator_class="HUMAN", approved_waves=[2],
                        mode="interactive", user_message="Approve these exact redirect repairs.",
                        graph_mutation_allowed=True, approved_plan_digest=self.plan["plan_digest"])
        metadata.update(overrides)
        entry = HITLSQLiteStore(self.root.parent / "ledger/hitl_decision_ledger.sqlite").record_decision(
            decision_id="fixture-" + digest(metadata), category="redirect_repair",
            prompt="Test fixture approval", options=[{"key": "approve_redirect_repair", "score": 0.5}],
            selected_option={"key": "approve_redirect_repair"}, metadata=metadata,
        )
        return entry["entry_id"]

    def reseal(self):
        self.plan.pop("plan_digest", None)
        self.plan["plan_digest"] = digest(self.plan)

    def apply(self, **kwargs):
        params = dict(plan=self.plan, approval_id=self.approval, run_id="fixture-run",
                      runtime_root=self.root)
        params.update(kwargs)
        return apply_consolidation_plan(self.staged, **params)

    def rows(self, path, table):
        with GovernedStorageContext.read_context(path) as conn:
            return conn.execute('SELECT * FROM "' + table + '" ORDER BY 1').fetchall()

    def manager(self):
        def validate_pair(pair):
            for kind in ("graph", "sidecar"):
                if sha256_file(self.root / pair[kind + "_path"]) != pair[kind + "_sha256"]:
                    raise ValueError("fixture pair hash mismatch")
            return {"status": "PASS"}
        return SimpleNamespace(
            runtime_root=self.root, generations_dir=self.root / "generations",
            releases_dir=self.root / "releases", active_path=self.pointer,
            lock_path=self.root / "active_pair.lock", active_pair=lambda: read_json(self.pointer),
            validate_pair=validate_pair, _verify_backend_snapshot=Mock(),
            _relative=lambda path: str(path.relative_to(self.root)),
        )

    def backend(self):
        return SimpleNamespace(backend_id="fixture-only", production_qualified=False,
                               model_id="fixture-only", model_revision="fixture-only",
                               model_manifest_digest="fixture-only")

    def build_test_sidecar(self, **kwargs):
        # An empty metadata DB for lifecycle wiring, not retrieval/vector qualification.
        shutil.copyfile(self.sidecar, kwargs["sidecar_path"])
        return {"sidecar_sha256": sha256_file(kwargs["sidecar_path"]), "record_count": 0}
