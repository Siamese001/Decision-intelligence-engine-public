"""
dual_pipeline/integrations/chatops.py

ChatOps integration for Slack Block Kit and Microsoft Teams Adaptive Cards.
Constructs atomic, clickable, confidence-scored interactive review cards
for Human-In-The-Loop decisions and verifies incoming webhook callbacks.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HITLOption:
    """An enumerated option presented to the operator."""

    option_id: str
    text: str
    confidence: float  # 0.0 to 1.0
    recommended: bool = False
    rationale: Optional[str] = None


def build_slack_block_kit_hitl_card(
    decision_id: str,
    directive_id: str,
    question: str,
    options: List[HITLOption],
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Builds a Slack Block Kit payload with interactive buttons for an atomic HITL decision.
    """
    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🤖 NEE Knowledge Graph — Operator Action Required",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Directive:* `{directive_id}`\n*Decision ID:* `{decision_id}`\n\n*{question}*",
            },
        },
    ]

    if context:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"ℹ️ *Context:* {context}"}],
        })

    blocks.append({"type": "divider"})

    # Action buttons
    elements: List[Dict[str, Any]] = []
    for opt in options:
        conf_pct = int(round(opt.confidence * 100))
        btn_text = f"({'★ ' if opt.recommended else ''}{opt.text} ({conf_pct}%)"
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": btn_text[:75]},
            "style": "primary" if opt.recommended else "default",
            "action_id": f"hitl_resolve_{opt.option_id}",
            "value": json.dumps({
                "decision_id": decision_id,
                "directive_id": directive_id,
                "selected_option": opt.option_id,
            }),
        })

    blocks.append({
        "type": "actions",
        "block_id": f"block_hitl_{decision_id}",
        "elements": elements,
    })

    return {"blocks": blocks}


def build_teams_adaptive_card_hitl_card(
    decision_id: str,
    directive_id: str,
    question: str,
    options: List[HITLOption],
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Builds a Microsoft Teams Adaptive Card (v1.5) payload for an atomic HITL decision.
    """
    body: List[Dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Medium",
            "weight": "Bolder",
            "text": "NEE Knowledge Graph — Operator Action Required",
        },
        {
            "type": "FactSet",
            "facts": [
                {"title": "Directive", "value": directive_id},
                {"title": "Decision ID", "value": decision_id},
            ],
        },
        {
            "type": "TextBlock",
            "text": question,
            "wrap": True,
            "weight": "Bolder",
        },
    ]

    if context:
        body.append({
            "type": "TextBlock",
            "text": f"Context: {context}",
            "isSubtle": True,
            "wrap": True,
        })

    actions: List[Dict[str, Any]] = []
    for opt in options:
        conf_pct = int(round(opt.confidence * 100))
        title = f"{'★ ' if opt.recommended else ''}{opt.text} ({conf_pct}%)"
        actions.append({
            "type": "Action.Submit",
            "title": title,
            "data": {
                "decision_id": decision_id,
                "directive_id": directive_id,
                "selected_option": opt.option_id,
            },
        })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": body,
        "actions": actions,
    }


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: str | bytes,
    signature: str,
    max_skew_seconds: int = 300,
) -> bool:
    """Verifies that an incoming Slack webhook request was signed with the app signing secret."""
    try:
        req_time = int(timestamp)
    except (ValueError, TypeError):
        return False

    if abs(int(time.time()) - req_time) > max_skew_seconds:
        return False

    body_bytes = body if isinstance(body, bytes) else body.encode("utf-8")
    sig_basestring = f"v0:{timestamp}:".encode("utf-8") + body_bytes
    my_signature = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        sig_basestring,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(my_signature, signature)


def parse_slack_interaction_payload(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Extracts decision resolution metadata from a Slack interaction callback payload.
    Returns dict with decision_id, directive_id, selected_option, and user_name.
    """
    actions = payload.get("actions", [])
    if not actions:
        return None
    val_str = actions[0].get("value")
    if not val_str:
        return None
    try:
        val_data = json.loads(val_str)
        val_data["operator"] = payload.get("user", {}).get("username") or payload.get("user", {}).get("name", "slack_operator")
        return val_data
    except Exception as e:
        logger.warning("Could not parse Slack action value: %s", e)
        return None
