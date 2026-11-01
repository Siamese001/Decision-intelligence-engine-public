"""
dual_pipeline/api/server.py

REST API server mode for meeting ingestion, directive management, and HITL gate query/resolution.
Includes OpenAPI 3.0 specification generation, Bearer token authentication, and health checks.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


@dataclass
class APIResponse:
    """Standardized REST API response envelope."""

    status_code: int
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        payload: Dict[str, Any] = {
            "status": "success" if 200 <= self.status_code < 300 else "error",
            "status_code": self.status_code,
            "timestamp": self.timestamp,
        }
        if self.data is not None:
            payload["data"] = self.data
        if self.error is not None:
            payload["error"] = self.error
        return json.dumps(payload, indent=2)


def generate_openapi_spec() -> Dict[str, Any]:
    """Generates an OpenAPI 3.0 specification for the NEE Pipeline REST API."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "NEE Decision Intelligence Engine REST API",
            "version": "1.0.0",
            "description": "Governed meeting ingestion, directive lifecycle, and HITL resolution endpoints.",
        },
        "paths": {
            "/v1/health": {
                "get": {
                    "summary": "System Health Check",
                    "responses": {"200": {"description": "Service healthy"}},
                }
            },
            "/v1/ingest/meeting": {
                "post": {
                    "summary": "Submit meeting for governed ingestion",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["input_path", "project", "meeting_name", "date"],
                                    "properties": {
                                        "input_path": {"type": "string"},
                                        "project": {"type": "string"},
                                        "meeting_name": {"type": "string"},
                                        "date": {"type": "string"},
                                        "attendees": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"202": {"description": "Ingestion job accepted"}},
                }
            },
            "/v1/directives/{directive_id}": {
                "get": {
                    "summary": "Get directive status and run execution state",
                    "parameters": [{"name": "directive_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Directive found"}, "404": {"description": "Not found"}},
                }
            },
            "/v1/hitl/pending": {
                "get": {
                    "summary": "List pending human-in-the-loop review items",
                    "responses": {"200": {"description": "List of pending decisions"}},
                }
            },
            "/v1/hitl/resolve": {
                "post": {
                    "summary": "Submit decision resolution for a pending HITL gate",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["decision_id", "selected_option", "operator"],
                                    "properties": {
                                        "decision_id": {"type": "string"},
                                        "selected_option": {"type": "string"},
                                        "operator": {"type": "string"},
                                        "rationale": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Decision resolved"}},
                }
            },
        },
    }


class APIRouter:
    """Lightweight in-memory router for REST API execution and testing."""

    def __init__(self, auth_token: Optional[str] = None) -> None:
        self.auth_token = auth_token
        self._routes: Dict[str, Dict[str, Callable[[Dict[str, Any], Dict[str, str]], APIResponse]]] = {
            "GET": {},
            "POST": {},
        }
        self._register_default_routes()

    def _register_default_routes(self) -> None:
        self.register("GET", "/openapi.json", self._handle_openapi)
        self.register("GET", "/v1/health", self._handle_health)

    def register(
        self,
        method: str,
        path: str,
        handler: Callable[[Dict[str, Any], Dict[str, str]], APIResponse],
    ) -> None:
        self._routes.setdefault(method.upper(), {})[path] = handler

    def _handle_openapi(self, body: Dict[str, Any], params: Dict[str, str]) -> APIResponse:
        return APIResponse(status_code=200, data=generate_openapi_spec())

    def _handle_health(self, body: Dict[str, Any], params: Dict[str, str]) -> APIResponse:
        return APIResponse(status_code=200, data={"status": "healthy", "service": "nee-api-server", "uptime": "active"})

    def handle_request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> APIResponse:
        """Dispatches an incoming request through auth validation and route matching."""
        headers = headers or {}
        if self.auth_token:
            auth_header = headers.get("Authorization") or headers.get("authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return APIResponse(status_code=401, error="Unauthorized: Missing or invalid Bearer token")
            token = auth_header[7:].strip()
            if token != self.auth_token:
                return APIResponse(status_code=403, error="Forbidden: Token mismatch")

        parsed = urlparse(path)
        base_path = parsed.path
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        method_routes = self._routes.get(method.upper(), {})

        # Direct match
        if base_path in method_routes:
            return method_routes[base_path](body or {}, params)

        # Dynamic path matching (e.g. /v1/directives/{directive_id})
        for pattern, handler in method_routes.items():
            if "{" in pattern and "}" in pattern:
                prefix = pattern.split("{")[0]
                if base_path.startswith(prefix):
                    param_name = pattern[len(prefix):].split("}")[0].lstrip("{")
                    param_val = base_path[len(prefix) :]
                    if "/" not in param_val and param_val:
                        params[param_name] = param_val
                        return handler(body or {}, params)

        return APIResponse(status_code=404, error=f"Endpoint not found: {method} {base_path}")
