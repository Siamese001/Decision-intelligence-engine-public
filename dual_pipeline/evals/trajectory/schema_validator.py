"""
Tool Call Argument Schema Conformance Validator.

Enforces 2026+ OpenAI and Anthropic structured outputs invariants:
- Strict JSON Schema typing and constraints.
- Rejection of unknown properties (additionalProperties: false).
- Identification of missing required parameters and invalid types.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class ToolSchemaValidator:
    """Validates tool call arguments against registered JSON schema definitions."""

    def __init__(self, tool_schemas: Optional[Dict[str, Dict[str, Any]]] = None):
        """Initialize with optional dictionary mapping tool_name -> json_schema."""
        self._schemas: Dict[str, Dict[str, Any]] = tool_schemas or {}

    def register_schema(self, tool_name: str, schema: Dict[str, Any]) -> None:
        """Register or update a tool's JSON schema."""
        self._schemas[tool_name] = schema

    def validate_call(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """
        Validate tool call arguments against schema.
        
        Returns:
            (is_valid, list_of_error_reasons)
        """
        if tool_name not in self._schemas:
            # If no schema is registered, cannot assert violation; passes with warning note
            return True, []

        schema = self._schemas[tool_name]
        errors: List[str] = []

        # Enforce type object
        if not isinstance(arguments, dict):
            errors.append(f"Arguments for '{tool_name}' must be a dictionary/object, got {type(arguments).__name__}")
            return False, errors

        properties = schema.get("properties", {})
        required = schema.get("required", [])
        allow_additional = schema.get("additionalProperties", False)

        # Check required fields
        for req_field in required:
            if req_field not in arguments:
                errors.append(f"Missing required parameter '{req_field}' in call to '{tool_name}'")

        # Check unexpected fields if additionalProperties is False
        if not allow_additional:
            for arg_key in arguments:
                if arg_key not in properties:
                    errors.append(
                        f"Unexpected property '{arg_key}' in call to '{tool_name}' (additionalProperties=false)"
                    )

        # Validate types for present arguments
        for arg_key, arg_val in arguments.items():
            if arg_key in properties:
                prop_spec = properties[arg_key]
                type_err = self._validate_property_type(arg_key, arg_val, prop_spec, tool_name)
                if type_err:
                    errors.append(type_err)

        return len(errors) == 0, errors

    def _validate_property_type(
        self, prop_name: str, value: Any, prop_spec: Dict[str, Any], tool_name: str
    ) -> Optional[str]:
        """Validate single property against its type specification."""
        expected_type = prop_spec.get("type")
        if not expected_type:
            return None

        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        # Handle boolean edge-case where isinstance(True, int) is True in Python
        if expected_type in ("integer", "number") and isinstance(value, bool):
            return f"Parameter '{prop_name}' in '{tool_name}' expected {expected_type}, got boolean"

        expected_py_type = type_map.get(expected_type)
        if expected_py_type and not isinstance(value, expected_py_type):
            return (
                f"Parameter '{prop_name}' in '{tool_name}' expected {expected_type}, "
                f"got {type(value).__name__}"
            )

        # Check enum if specified
        if "enum" in prop_spec and value not in prop_spec["enum"]:
            return (
                f"Parameter '{prop_name}' in '{tool_name}' value '{value}' not in allowed enum: "
                f"{prop_spec['enum']}"
            )

        # Check string length bounds
        if isinstance(value, str):
            min_len = prop_spec.get("minLength")
            max_len = prop_spec.get("maxLength")
            if min_len is not None and len(value) < min_len:
                return f"Parameter '{prop_name}' in '{tool_name}' length {len(value)} < minLength {min_len}"
            if max_len is not None and len(value) > max_len:
                return f"Parameter '{prop_name}' in '{tool_name}' length {len(value)} > maxLength {max_len}"

        return None
