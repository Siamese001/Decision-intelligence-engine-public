"""
dual_pipeline/alerting.py

Alert dispatcher for pipeline failures, security incidents, and operational bottlenecks.
Supports Slack incoming webhooks, PagerDuty Events API, and generic HTTP endpoints with
fail-safe non-blocking execution.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dual_pipeline.credentials import CredentialManager

logger = logging.getLogger("dual_pipeline.alerting")

_ALERT_HISTORY: List[Dict[str, Any]] = []
_ALERT_LOCK = threading.Lock()


def get_dispatched_alerts() -> List[Dict[str, Any]]:
    """Retrieve in-memory alert dispatch history (for test assertions)."""
    with _ALERT_LOCK:
        return list(_ALERT_HISTORY)


def clear_dispatched_alerts() -> None:
    """Clear in-memory alert dispatch history."""
    with _ALERT_LOCK:
        _ALERT_HISTORY.clear()


class AlertDispatcher:
    """Dispatches operational alerts to configured external endpoints."""

    def __init__(
        self,
        slack_webhook_url: Optional[str] = None,
        pagerduty_routing_key: Optional[str] = None,
        generic_webhook_url: Optional[str] = None,
    ) -> None:
        self.slack_url = slack_webhook_url or CredentialManager.get_credential("SLACK_WEBHOOK_URL")
        self.pagerduty_key = pagerduty_routing_key or CredentialManager.get_credential("PAGERDUTY_ROUTING_KEY")
        self.generic_url = generic_webhook_url or CredentialManager.get_credential("ALERT_WEBHOOK_URL")

    def _dispatch_slack(self, level: str, event_type: str, message: str, details: Optional[Dict[str, Any]]) -> bool:
        if not self.slack_url:
            return False
        try:
            slack_body = {
                "text": f"[{level}] *{event_type}*: {message}",
                "attachments": [
                    {
                        "color": "#FF0000" if level == "ERROR" else "#FFA500",
                        "fields": [{"title": k, "value": str(v), "short": True} for k, v in (details or {}).items()],
                        "ts": int(datetime.now(timezone.utc).timestamp()),
                    }
                ],
            }
            self._post_json(self.slack_url, slack_body)
            return True
        except Exception as e:
            logger.warning("Failed to dispatch Slack alert: %s", e)
            return False

    def _dispatch_pagerduty(self, level: str, event_type: str, message: str, details: Optional[Dict[str, Any]]) -> bool:
        if not self.pagerduty_key:
            return False
        try:
            pd_body = {
                "routing_key": self.pagerduty_key,
                "event_action": "trigger",
                "payload": {
                    "summary": f"[{level}] {event_type}: {message}",
                    "source": "decision-intelligence-engine",
                    "severity": "critical" if level == "ERROR" else "warning",
                    "custom_details": details or {},
                },
            }
            self._post_json("https://events.pagerduty.com/v2/enqueue", pd_body)
            return True
        except Exception as e:
            logger.warning("Failed to dispatch PagerDuty alert: %s", e)
            return False

    def _dispatch_generic(self, alert_payload: Dict[str, Any]) -> bool:
        if not self.generic_url:
            return False
        try:
            self._post_json(self.generic_url, alert_payload)
            return True
        except Exception as e:
            logger.warning("Failed to dispatch generic webhook alert: %s", e)
            return False

    def dispatch(
        self,
        event_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        level: str = "ERROR",
    ) -> bool:
        """
        Dispatches an alert to configured webhooks. Fail-safe: errors in alert transmission
        are logged as warnings and never propagate as unhandled exceptions.
        """
        ts = datetime.now(timezone.utc).isoformat()
        alert_payload = {
            "timestamp": ts,
            "event_type": event_type,
            "level": level,
            "message": message,
            "details": details or {},
        }

        with _ALERT_LOCK:
            _ALERT_HISTORY.append(alert_payload)

        d_slack = self._dispatch_slack(level, event_type, message, details)
        d_pd = self._dispatch_pagerduty(level, event_type, message, details)
        d_gen = self._dispatch_generic(alert_payload)
        return d_slack or d_pd or d_gen

    def _post_json(self, url: str, data: Dict[str, Any], timeout: float = 5.0) -> None:
        raw_bytes = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=raw_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            if status >= 400:
                logger.warning("Webhook POST to %s returned HTTP %s", url, status)


_GLOBAL_DISPATCHER = AlertDispatcher()


def dispatch_pipeline_alert(
    event_type: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    level: str = "ERROR",
) -> bool:
    """Convenience helper to dispatch alert via global singleton dispatcher."""
    return _GLOBAL_DISPATCHER.dispatch(event_type=event_type, message=message, details=details, level=level)
