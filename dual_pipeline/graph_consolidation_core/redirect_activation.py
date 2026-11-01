"""Additional release gates for exact redirect repairs (called under the pair lock)."""
from pathlib import Path

from ..core import sha256_file
from ..release_core.helpers import _verify_receipt
from ..retrieval_release import validate_activation_evaluation
from .redirect_guard import verify_redirect_context
from .redirect_repair import is_redirect_plan, redirect_operations


def verify_redirect_activation(manager, receipt, approval_id):
    _verify_receipt(receipt)
    writes = receipt.get("graph_write_receipt", {})
    plan = receipt.get("redirect_repair_plan")
    has_redirect = any(op.get("operation_type") == "flatten_redirect"
                       for op in writes.get("operations", []))
    if plan is None and not has_redirect:
        return
    if not isinstance(plan, dict) or not is_redirect_plan(plan):
        raise ValueError("redirect release is missing its exact approved plan")
    if approval_id != receipt["approval_id"] or receipt["status"] != "PREPARED":
        raise PermissionError("redirect activation approval or status mismatch")
    ops = redirect_operations(plan)
    if receipt["change_set_digest"] != plan["plan_digest"]:
        raise ValueError("redirect release plan digest mismatch")
    recorded = [{key: op[key] for key in ops[0]} for op in writes["operations"]]
    if recorded != ops or writes["operation_count"] != len(ops):
        raise ValueError("redirect release operation scope mismatch")
    root = Path(manager.runtime_root).resolve()
    target = root / receipt["next_pair"]["graph_path"]
    active = verify_redirect_context(root, plan, approval_id, target)
    if receipt["previous_pair"] != active:
        raise RuntimeError("redirect release previous pair mismatch")
    if sha256_file(target) != writes["graph_sha256_after"]:
        raise RuntimeError("redirect staged graph changed")
    manager.validate_pair(receipt["next_pair"])
    validate_activation_evaluation(root, receipt)
