"""Read-only snapshot, rollback, approval, and staging boundaries for redirect repair."""
from __future__ import annotations

import json
from pathlib import Path

from ..core import digest, read_json, sha256_file
from ..hitl_governance import validate_approval_origin
from ..storage.governed_context import GovernedStorageContext, Row
from .redirect_repair import redirect_operations


def _unchanged_file(path, expected):
    if not path.is_file() or sha256_file(path) != expected:
        raise RuntimeError("redirect repair snapshot or backup changed: " + str(path))
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError("redirect repair requires a checkpointed immutable snapshot")


def verify_redirect_snapshot(runtime_root, plan):
    redirect_operations(plan)
    root = Path(runtime_root).resolve()
    if str(root) != plan["runtime_root"]:
        raise ValueError("redirect repair runtime root mismatch")
    pointer = root / "active_pair.json"
    _unchanged_file(pointer, plan["active_pair_sha256"])
    active = read_json(pointer)
    if active["generation_id"] != plan["base_generation_id"]:
        raise RuntimeError("redirect repair active generation changed")
    expected = {pointer.resolve(): plan["active_pair_sha256"]}
    for kind in ("graph", "sidecar"):
        path = (root / active[kind + "_path"]).resolve()
        value = plan["base_" + kind + "_sha256"]
        if active[kind + "_sha256"] != value:
            raise RuntimeError("redirect repair active hash binding changed")
        _unchanged_file(path, value)
        expected[path] = value
    _verify_backup(root, plan, expected)
    return active


def _verify_backup(root, plan, expected):
    manifest_path = Path(plan["backup_manifest_path"])
    if not manifest_path.resolve().is_relative_to(root.parent / "hygiene_backups"):
        raise ValueError("rollback manifest is outside the governed backup directory")
    _unchanged_file(manifest_path, plan["backup_manifest_sha256"])
    manifest = read_json(manifest_path)
    if manifest.get("generation_id") != plan["base_generation_id"]:
        raise RuntimeError("rollback generation mismatch")
    files = manifest.get("files", [])
    if len(files) != len(expected):
        raise RuntimeError("rollback manifest must bind all three snapshot files")
    seen = set()
    for entry in files:
        source, backup = Path(entry["source"]).resolve(), Path(entry["backup"]).resolve()
        if source in seen or source not in expected or entry["sha256"] != expected[source]:
            raise RuntimeError("rollback source or hash binding mismatch")
        if backup in expected or not backup.is_relative_to(manifest_path.parent.resolve()):
            raise RuntimeError("rollback copy must be independent and contained")
        _unchanged_file(backup, expected[source])
        if backup.samefile(source):
            raise RuntimeError("rollback copy aliases the live snapshot")
        seen.add(source)


def verify_redirect_approval(runtime_root, plan, approval_id):
    ledger = Path(runtime_root).resolve().parent / "ledger/hitl_decision_ledger.sqlite"
    with GovernedStorageContext.read_context(ledger) as conn:
        conn.row_factory = Row
        row = conn.execute("SELECT * FROM hitl_decisions WHERE entry_id=?", (approval_id,)).fetchone()
    if row is None:
        raise PermissionError("redirect repair requires an authoritative ledger entry")
    record = dict(row)
    declared = record.pop("entry_digest")
    for key in ("options", "selected_option", "metadata"):
        record[key] = json.loads(record[key])
    if digest(record) != declared:
        raise PermissionError("redirect approval ledger digest mismatch")
    meta = record["metadata"]
    expected = {
        "source": "user_input", "operator_class": "HUMAN",
        "approved_plan_digest": plan["plan_digest"], "graph_mutation_allowed": True,
        "approved_waves": [2], "mode": "interactive",
    }
    if any(meta.get(key) != value for key, value in expected.items()):
        raise PermissionError("redirect approval does not authorize this exact live wave")
    if meta.get("graph_mutation_allowed") is not True:
        raise PermissionError("redirect approval requires explicit mutation permission")
    if record["category"] != "redirect_repair" or record["selected_key"] != "approve_redirect_repair":
        raise PermissionError("redirect approval decision category or choice mismatch")
    message = meta.get("user_message")
    if not isinstance(message, str) or not message.strip():
        raise PermissionError("redirect approval has no human message")
    valid, reason = validate_approval_origin({"source": "user_input", "text": message})
    if not valid:
        raise PermissionError(reason)


def verify_redirect_context(runtime_root, plan, approval_id, graph_path=None):
    if runtime_root is None:
        raise PermissionError("redirect repair requires an explicit governed runtime")
    active = verify_redirect_snapshot(runtime_root, plan)
    verify_redirect_approval(runtime_root, plan, approval_id)
    if active.get("production_qualified") is not False:
        raise PermissionError("redirect repair has no enterprise production authorization")
    if graph_path is not None:
        root, target = Path(runtime_root).resolve(), Path(graph_path).resolve()
        live = (root / active["graph_path"]).resolve()
        if target.name != "graph.sqlite" or target.parent.parent != root / "generations":
            raise PermissionError("redirect repair target must be an isolated generation")
        if target.stat().st_nlink != 1:
            raise PermissionError("redirect target must not share storage with another file")
        if target.parent == live.parent or target.samefile(live):
            raise PermissionError("redirect repair must not write the active generation")
    return active
