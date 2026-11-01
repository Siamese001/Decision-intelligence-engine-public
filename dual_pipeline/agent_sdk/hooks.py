"""Lifecycle hooks for Antigravity SDK agents in Decision Intelligence.

Provides hook implementations for:
1. Session start/end telemetry tracking
2. Pre-tool call security & policy decision checks
3. Post-tool execution duration and outcome auditing
4. Stop hook gating verifying completion criteria before trajectory conclusion
"""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from google.antigravity import types
from google.antigravity.hooks import hooks
from google.antigravity.hooks.policy import Decision
from google.antigravity.types import HookResult, StopArgs, StopDecision, StopHookResult

from dual_pipeline.agent_sdk.policies import NeeSecurityPolicy

VALID_RECEIPT_OUTCOMES = frozenset({"PASS", "COMPLETE", "SUCCESS"})


def _search_pending_history(
    history: List[Dict[str, Any]], tool_id: Optional[str], tool_name: Optional[str]
) -> Optional[Dict[str, Any]]:
    pending = [e for e in reversed(history) if e.get("completed_at") is None]
    if tool_id:
        match = next((e for e in pending if e.get("id") == tool_id), None)
        if match:
            return match
    if tool_name:
        match = next((e for e in pending if e.get("name") == tool_name), None)
        if match:
            return match
    return pending[0] if pending else None


def _extract_receipt_data(receipt_p: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        content = receipt_p.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        return None, f"Receipt '{receipt_p}' is malformed JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"Receipt '{receipt_p}' must be a JSON object."
    return data, None


def _get_first_str_field(data: Dict[str, Any], fields: Tuple[str, ...]) -> Optional[str]:
    for f in fields:
        val = data.get(f)
        if val is not None:
            return str(val)
    return None


def _validate_receipt_outcome(data: Dict[str, Any]) -> Optional[str]:
    outcome = _get_first_str_field(data, ("status", "terminal_outcome", "outcome"))
    if not outcome or outcome.upper() not in VALID_RECEIPT_OUTCOMES:
        return f"Receipt outcome '{outcome}' does not indicate completion."
    return None


def _validate_receipt_digest(data: Dict[str, Any]) -> Optional[str]:
    digest = _get_first_str_field(data, ("receipt_digest", "digest", "merkle_root", "sha256"))
    if not digest or len(digest.strip()) < 16:
        return "Receipt is missing a valid cryptographic digest."
    return None


def _validate_receipt_bindings(
    data: Dict[str, Any],
    receipt_p: Path,
    expected_run_id: Optional[str],
    expected_plan_path: Optional[str],
    min_receipt_timestamp: Optional[float],
) -> Optional[str]:
    if expected_run_id:
        found_run = _get_first_str_field(data, ("run_id", "session_id"))
        if found_run and found_run != expected_run_id:
            return f"Receipt run ID mismatch: expected '{expected_run_id}', found '{found_run}'."

    if expected_plan_path:
        found_plan = _get_first_str_field(data, ("plan_path", "plan"))
        if found_plan and found_plan != str(expected_plan_path):
            return f"Receipt plan path mismatch: expected '{expected_plan_path}', found '{found_plan}'."

    if min_receipt_timestamp is not None and receipt_p.stat().st_mtime < min_receipt_timestamp:
        return f"Receipt is stale: modified at {receipt_p.stat().st_mtime} prior to threshold {min_receipt_timestamp}."

    return None


class NeeLifecycleHooks:
    """Factory and registry for Antigravity lifecycle hooks."""

    def __init__(self, run_id: Optional[str] = None) -> None:
        self.run_id = run_id or f"sdk_run_{int(time.time())}"
        self.created_at = time.time()
        self.tool_call_history: List[Dict[str, Any]] = []
        self.session_started: bool = False
        self.session_ended: bool = False

    def build_pre_tool_call_decide(self) -> Callable[[types.ToolCall], Any]:
        async def _pre_tool_hook(tool_call: types.ToolCall) -> HookResult:
            args = getattr(tool_call, "arguments", getattr(tool_call, "args", {}))
            decision = NeeSecurityPolicy.evaluate_static_decision(tool_call.name, arguments=args)
            call_id = getattr(tool_call, "id", None) or f"call_{len(self.tool_call_history) + 1}"
            entry = {
                "id": call_id,
                "name": tool_call.name,
                "args": args if isinstance(args, dict) else {},
                "timestamp": time.time(),
                "decision": decision.value,
                "status": "APPROVED" if decision == Decision.APPROVE else ("DENIED" if decision == Decision.DENY else "INTERCEPTED"),
                "completed_at": None,
                "error": None,
            }
            self.tool_call_history.append(entry)

            if decision == Decision.DENY:
                # Deny execution immediately; deny result is strictly non-execution, never a success choice
                return HookResult(allow=False)

            return HookResult(allow=True)


        return _pre_tool_hook

    def _find_pending_tool_call(self, tool_id: Optional[str], tool_name: Optional[str]) -> Optional[Dict[str, Any]]:
        return _search_pending_history(self.tool_call_history, tool_id, tool_name)

    def _correlate_tool_completion(self, tool_data: Any) -> None:
        tool_id = getattr(tool_data, "id", None) or (tool_data.get("id") if isinstance(tool_data, dict) else None)
        tool_name = getattr(tool_data, "name", None) or (tool_data.get("name") if isinstance(tool_data, dict) else None)
        entry = self._find_pending_tool_call(tool_id, tool_name)
        if not entry:
            return
        entry["completed_at"] = time.time()
        err = getattr(tool_data, "error", None) or (tool_data.get("error") if isinstance(tool_data, dict) else None)
        if err:
            entry["error"] = str(err)
            entry["status"] = "FAILED"
        else:
            entry["status"] = "COMPLETED"

    def build_post_tool_call(self) -> Callable[[Any], Any]:
        """Post-tool hook recording completion telemetry and correlating to initiating call."""

        async def _post_tool_hook(tool_data: Any) -> None:
            self._correlate_tool_completion(tool_data)

        return _post_tool_hook

    def _validate_receipt_payload(
        self,
        receipt_p: Path,
        expected_run_id: Optional[str] = None,
        expected_plan_path: Optional[str] = None,
        min_receipt_timestamp: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Validates JSON structure, outcome, digest, and scope bindings of a receipt."""
        data, err = _extract_receipt_data(receipt_p)
        if err or data is None:
            return False, err or "Invalid receipt data."

        err = _validate_receipt_outcome(data) or _validate_receipt_digest(data)
        if err:
            return False, err

        err = _validate_receipt_bindings(
            data, receipt_p, expected_run_id, expected_plan_path, min_receipt_timestamp
        )
        if err:
            return False, err

        return True, ""

    def build_stop_hook(
        self,
        enforce_receipt_verification: bool = True,
        required_receipt_path: Optional[Union[str, Path]] = None,
        min_receipt_timestamp: Optional[float] = None,
        expected_run_id: Optional[str] = None,
        expected_plan_path: Optional[str] = None,
        receipt_validator: Optional[Callable[[Path], Tuple[bool, str]]] = None,
    ) -> Callable[[StopArgs], Any]:
        """Stop hook verifying that completion requirements were met before allowing agent stop."""

        async def _stop_hook(data: StopArgs) -> StopHookResult:
            if enforce_receipt_verification and data.continuation_count == 0 and not self.tool_call_history:
                return StopHookResult(
                    decision=StopDecision.CONTINUE,
                    reason="Trajectory reached idle without executing required domain tools or checks.",
                )

            if required_receipt_path is not None:
                receipt_p = Path(required_receipt_path)
                if not receipt_p.is_file():
                    return StopHookResult(
                        decision=StopDecision.CONTINUE,
                        reason=f"Required receipt '{receipt_p}' is missing or not a file.",
                    )

                if receipt_validator is not None:
                    ok, err = receipt_validator(receipt_p)
                else:
                    ok, err = self._validate_receipt_payload(
                        receipt_p,
                        expected_run_id=expected_run_id,
                        expected_plan_path=expected_plan_path,
                        min_receipt_timestamp=min_receipt_timestamp,
                    )

                if not ok:
                    return StopHookResult(
                        decision=StopDecision.CONTINUE,
                        reason=f"Receipt validation failed: {err}",
                    )

            return StopHookResult(decision=StopDecision.ALLOW_STOP)

        return _stop_hook

    def build_all_hooks(
        self,
        enforce_receipt_verification: bool = True,
        required_receipt_path: Optional[Union[str, Path]] = None,
        min_receipt_timestamp: Optional[float] = None,
        expected_run_id: Optional[str] = None,
        expected_plan_path: Optional[str] = None,
        receipt_validator: Optional[Callable[[Path], Tuple[bool, str]]] = None,
    ) -> List[Any]:
        """Collect all decorated lifecycle hooks into a list for LocalAgentConfig."""
        pre_tool_fn = self.build_pre_tool_call_decide()
        post_tool_fn = self.build_post_tool_call()
        stop_fn = self.build_stop_hook(
            enforce_receipt_verification=enforce_receipt_verification,
            required_receipt_path=required_receipt_path,
            min_receipt_timestamp=min_receipt_timestamp,
            expected_run_id=expected_run_id,
            expected_plan_path=expected_plan_path,
            receipt_validator=receipt_validator,
        )

        return [
            hooks.pre_tool_call_decide(pre_tool_fn),
            hooks.post_tool_call(post_tool_fn),
            hooks.stop(stop_fn),
        ]
