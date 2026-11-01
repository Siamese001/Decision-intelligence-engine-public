"""Text normalization and instruction envelope extraction for query routing."""
from __future__ import annotations

import unicodedata

from ..core import normalize_space
from .constants import (
    _META_ROUTING_TOKEN,
    _META_WRAPPER,
    _NEGATED_CLAUSE,
    _NEGATED_PREPOSITIONAL_CLAUSE,
    _OUTER_INSTRUCTION_WRAPPERS,
    _QUOTED_OR_CODE,
    _REQUEST_TEXT_ENVELOPE,
)
from .models import RoutingText


def _unwrap_single_outer_instruction_wrapper(text: str) -> tuple[str, bool]:
    """Return one unambiguous whole-request quote/code body, if present.

    A quote or code delimiter remains non-instruction context unless it wraps
    the complete request.  The body cannot contain that delimiter itself, so
    nested or multi-part quoted payloads are not recursively promoted into an
    instruction.  This keeps the exception intentionally narrower than the
    existing quoted-payload shield.
    """

    if len(text) < 3 or text[0] not in _OUTER_INSTRUCTION_WRAPPERS:
        return text, False
    delimiter = text[0]
    if text[-1] != delimiter:
        return text, False
    body = normalize_space(text[1:-1])
    if not body or delimiter in body:
        return text, False
    return body, True


def _routing_text(question: str) -> RoutingText:
    """Remove non-instruction payloads before matching routing signals.

    The input itself is never sent anywhere. This is deliberately a compact,
    deterministic parser. An exact ``Request text: \"...\"`` envelope is a
    deliberate delegation wrapper. A single complete, unambiguous outer
    quote/code wrapper is also an instruction envelope; other quoted/code
    payloads and explicit negative clauses cannot become a second destination
    instruction.
    """

    normalized = unicodedata.normalize("NFKC", question).translate(
        str.maketrans({character: "-" for character in "\u2010\u2011\u2012\u2013\u2014\u2212"})
    )
    normalized = normalize_space(normalized.replace("\u200b", ""))
    normalized = _META_WRAPPER.sub("", normalized)
    envelope = _REQUEST_TEXT_ENVELOPE.match(normalized)
    delegated_request = envelope is not None
    if envelope is not None:
        normalized = normalize_space(envelope.group("body"))
    outer_wrapper_unwrapped = False
    if not delegated_request:
        normalized, outer_wrapper_unwrapped = _unwrap_single_outer_instruction_wrapper(
            normalized
        )
    without_context = _QUOTED_OR_CODE.sub(" ", normalized)
    without_meta_token = _META_ROUTING_TOKEN.sub(" ", without_context)
    without_negation = _NEGATED_PREPOSITIONAL_CLAUSE.sub(" ", without_meta_token)
    without_negation = _NEGATED_CLAUSE.sub(" ", without_negation)
    instruction = normalize_space(without_negation)
    return RoutingText(
        normalized=normalized,
        instruction=instruction,
        ignored_context=instruction != normalized and without_context != normalized,
        removed_negation=without_negation != without_meta_token,
        delegated_request=delegated_request,
        outer_wrapper_unwrapped=outer_wrapper_unwrapped,
    )
