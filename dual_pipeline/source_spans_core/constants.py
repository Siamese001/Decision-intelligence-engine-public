"""Constants and regex patterns for source span extraction."""
from __future__ import annotations

import re

EXTRACTOR_NAME = "nee-a2a-source-span-extractor"
EXTRACTOR_VERSION = "1.0.0"
SELECTION_METHOD = "deterministic_lexical_v1"
SELECTION_SCHEMA_VERSION = "1.1.0"
HUMAN_ATTESTATION = (
    "I attest that I personally reviewed the exact frozen source quote, offsets, and "
    "provenance for every decision in this ledger."
)
CONTEXT_CHARACTERS = 256
MAX_QUOTE_CHARACTERS = 1_600

_HIDDEN_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
_HEADING_TAG = re.compile(r"h([1-6])$")
_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]*")
_SENTENCE_END = re.compile(r"[.!?]+(?:[\"'”’\)\]])?(?=\s|$)")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "with",
    }
)
