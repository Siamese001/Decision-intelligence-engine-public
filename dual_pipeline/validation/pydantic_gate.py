"""
dual_pipeline/validation/pydantic_gate.py

Wave 1: Token-Stream Syntax Repair & Strict Pydantic Gates.
Replaces generative formatting judges with deterministic token-aware parsers.
Uses a Pushdown Automaton (PDA) to parse and repair JSON token streams without
corrupting string literals containing braces, brackets, or commas.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, Union

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class PydanticValidationError(ValueError):
    """Raised when deterministic Pydantic schema validation fails."""

    def __init__(self, message: str, errors: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.errors = errors or []


class DeterministicPDAParser:
    """Pushdown Automaton (PDA) token-stream parser and repair engine.

    Unlike regex-based string replacements which cannot parse Context-Free Grammars
    and inadvertently corrupt string literals containing '{' or '}', this PDA
    maintains explicit lexer state (in_string, escape, token_stack) to heal truncated
    or syntactically malformed JSON in <2ms with zero text generation or hallucination.
    """

    @staticmethod
    def strip_markdown_envelopes(raw: str) -> str:
        """Strip markdown fences (```json ... ```), conversational preamble, and whitespace."""
        text = raw.strip()
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fence_match:
            text = fence_match.group(1).strip()
        elif text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        if text.endswith("```"):
            text = text[:-3].strip()

        # If text does not start with JSON container but contains one, strip preamble
        if not text.startswith(("{", "[")):
            idx_brace = text.find("{")
            idx_bracket = text.find("[")
            indices = [i for i in (idx_brace, idx_bracket) if i != -1]
            if indices:
                text = text[min(indices):]
        return text

    @classmethod
    def _handle_closing_delimiter(
        cls, ch: str, stack: List[str], chars: List[str], actions: List[str], pos: int
    ) -> None:
        expected = "{" if ch == "}" else "["
        if stack and stack[-1] == expected:
            stack.pop()
            cls._strip_trailing_comma_before_close(chars)
            chars.append(ch)
        else:
            actions.append(f"Skipped unexpected closing delimiter '{ch}' at position {pos}")

    @staticmethod
    def _scan_string_char(ch: str, escaped: bool) -> Tuple[bool, bool]:
        if escaped:
            return True, False
        if ch == "\\":
            return True, True
        if ch == '"':
            return False, False
        return True, False

    @classmethod
    def _handle_comma_delimiter(cls, chars: List[str], actions: List[str]) -> None:
        chars.append(",")

    @classmethod
    def _scan_stream_tokens(
        cls, text: str, chars: List[str], stack: List[str], actions: List[str]
    ) -> bool:
        in_string = False
        escaped = False

        for i, ch in enumerate(text):
            if in_string:
                chars.append(ch)
                in_string, escaped = cls._scan_string_char(ch, escaped)
                continue

            if ch == '"':
                in_string = True
                escaped = False
                chars.append(ch)
            elif ch in ("{", "["):
                stack.append(ch)
                chars.append(ch)
            elif ch in ("}", "]"):
                cls._handle_closing_delimiter(ch, stack, chars, actions, i)
            elif ch == ",":
                cls._handle_comma_delimiter(chars, actions)
            else:
                chars.append(ch)

        return in_string

    @classmethod
    def heal_token_stream(cls, raw: str) -> Tuple[str, List[str]]:
        """Parse token stream with a PDA and heal structural truncations.

        Returns (healed_json_string, list_of_repair_actions).
        """
        text = cls.strip_markdown_envelopes(raw)
        if not text:
            return "{}", ["Empty input healed to empty object"]

        actions: List[str] = []
        stack: List[str] = []
        chars: List[str] = []

        in_string = cls._scan_stream_tokens(text, chars, stack, actions)
        cls._finalize_unclosed_tokens(chars, stack, in_string, actions)
        return "".join(chars), actions

    @classmethod
    def _finalize_unclosed_tokens(
        cls, chars: List[str], stack: List[str], in_string: bool, actions: List[str]
    ) -> None:
        """Close unclosed strings and containers from the PDA stack at EOF."""
        if in_string:
            if chars and chars[-1] == "\\":
                chars.pop()
                actions.append("Stripped dangling escape backslash at EOF")
            chars.append('"')
            actions.append("Closed unclosed string literal at EOF")

        cls._strip_trailing_comma_before_close(chars)
        cls._handle_dangling_colon_before_close(chars, actions)
        cls._handle_incomplete_literals_before_close(chars, actions)

        while stack:
            open_delim = stack.pop()
            if open_delim == "{":
                chars.append("}")
                actions.append("PDA: Injected closing brace '}' for unclosed object")
            elif open_delim == "[":
                chars.append("]")
                actions.append("PDA: Injected closing bracket ']' for unclosed array")

    @staticmethod
    def _handle_dangling_colon_before_close(chars: List[str], actions: List[str]) -> None:
        """If stream ends with a colon after a key without a value, inject null."""
        idx = len(chars) - 1
        while idx >= 0 and chars[idx] in (" ", "\t", "\n", "\r"):
            idx -= 1
        if idx >= 0 and chars[idx] == ":":
            chars.append(" null")
            actions.append("PDA: Injected 'null' for dangling key colon at EOF")

    @staticmethod
    def _handle_incomplete_literals_before_close(chars: List[str], actions: List[str]) -> None:
        """If stream ends with incomplete boolean/null literal, complete it."""
        idx = len(chars) - 1
        while idx >= 0 and chars[idx] in (" ", "\t", "\n", "\r"):
            idx -= 1
        tail = "".join(chars[max(0, idx - 4):idx + 1])
        if tail.endswith("tru") and not tail.endswith("stru"):
            chars.append("e")
            actions.append("PDA: Completed truncated boolean 'true'")
        elif tail.endswith("fal"):
            chars.append("se")
            actions.append("PDA: Completed truncated boolean 'false'")
        elif tail.endswith("nul"):
            chars.append("l")
            actions.append("PDA: Completed truncated literal 'null'")

    @staticmethod
    def _strip_trailing_comma_before_close(chars: List[str]) -> None:
        """Backtrack in char buffer past whitespace to strip a trailing comma if present."""
        idx = len(chars) - 1
        while idx >= 0 and chars[idx] in (" ", "\t", "\n", "\r"):
            idx -= 1
        if idx >= 0 and chars[idx] == ",":
            chars.pop(idx)


class TokenStreamJSONHealer:
    """High-level deterministic JSON healer and parser."""

    @classmethod
    def repair(cls, raw: str) -> Tuple[str, List[str]]:
        start_time = time.perf_counter()
        # Fast path: try native json.loads first
        clean = DeterministicPDAParser.strip_markdown_envelopes(raw)
        try:
            json.loads(clean)
            return clean, []
        except Exception:
            pass

        healed, actions = DeterministicPDAParser.heal_token_stream(raw)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        if actions:
            actions.append(f"PDA token-stream repair completed in {elapsed_ms:.2f}ms")
            logger.debug("TokenStreamJSONHealer applied: %s", actions)
        return healed, actions


def repair_json_tokens(raw: str) -> str:
    """Convenience functional interface for token-stream JSON repair."""
    healed, _ = TokenStreamJSONHealer.repair(raw)
    return healed


def validate_with_pydantic(
    model_cls: Type[T],
    raw_payload: Union[str, Dict[str, Any], List[Any]],
    auto_heal: bool = True,
) -> T:
    """Validate a JSON string or dict against a strict Pydantic model.

    If raw_payload is a string and fails JSON parsing, TokenStreamJSONHealer
    repairs the token stream deterministically without invoking external LLMs.
    """
    if isinstance(raw_payload, (dict, list)):
        data = raw_payload
    elif isinstance(raw_payload, str):
        text = raw_payload
        if auto_heal:
            healed, actions = TokenStreamJSONHealer.repair(text)
            if any("Injected 'null'" in a for a in actions):
                raise PydanticValidationError("Dangling key colon without value cannot be coerced to null in strict Pydantic gate.")
            text = healed
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PydanticValidationError(
                f"Deterministic JSON parse failed after token repair: {exc}"
            ) from exc
    else:
        raise PydanticValidationError(f"Unsupported payload type: {type(raw_payload)}")

    try:
        return model_cls.model_validate(data)
    except ValidationError as val_err:
        errors = val_err.errors()
        msg = f"Deterministic Pydantic validation failed for {model_cls.__name__}: {len(errors)} error(s)"
        raise PydanticValidationError(msg, errors=errors) from val_err
