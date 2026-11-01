"""Lexical tokenization routines for BM25 and question terms."""
from __future__ import annotations

import re

from .constants import _BM25_STOPWORDS, _QUERY_STOPWORDS


_ACRONYM_DOT_RE = re.compile(r"\b([a-z0-9])\.(?=[a-z0-9]|\b)")
_ACRONYM_AMP_RE = re.compile(r"\b([a-z])&([a-z])\b")
_ACRONYM_HYPHEN_RE = re.compile(r"\b([a-z])\-([0-9])\b")
_PERCENT_RANGE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)%")
_PERCENT_TOKEN_RE = re.compile(r"\b(\d+(?:\.\d+)?)%")
_RATIO_TOKEN_RE = re.compile(r"\b(\d+):(\d+)\b")


def _normalize_acronym_punct(text: str) -> str:
    cleaned = text.casefold()
    cleaned = _PERCENT_RANGE_RE.sub(r"\1pct \2pct", cleaned)
    cleaned = _PERCENT_TOKEN_RE.sub(r"\1pct", cleaned)
    cleaned = _RATIO_TOKEN_RE.sub(r"\1to\2", cleaned)
    cleaned = _ACRONYM_DOT_RE.sub(r"\1", cleaned)
    cleaned = _ACRONYM_AMP_RE.sub(r"\1\2", cleaned)
    return _ACRONYM_HYPHEN_RE.sub(r"\1\2", cleaned)


def _question_tokens(question: str) -> set[str]:
    normalized = _normalize_acronym_punct(question)
    return {
        value
        for value in re.findall(r"[a-z0-9]+", normalized)
        if len(value) >= 2 and value not in _QUERY_STOPWORDS
    }


def bm25_query_tokens(question: str) -> list[str]:
    """Return the one deterministic lexical token projection used by BM25."""

    normalized = _normalize_acronym_punct(question)
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if token not in _BM25_STOPWORDS and len(token) >= 2
    ]
    return list(dict.fromkeys(tokens))[:32]
