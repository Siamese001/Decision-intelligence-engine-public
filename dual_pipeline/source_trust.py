"""Pinned first-party host checks for externally captured vendor sources."""

from __future__ import annotations

from urllib.parse import urlparse

_VENDOR_HOSTS = {
    "openai": frozenset({"openai.github.io", "openai.com", "www.openai.com"}),
    "anthropic": frozenset({"anthropic.com", "www.anthropic.com", "docs.anthropic.com"}),
}


def assert_pinned_vendor_host(vendor: object, url: object) -> None:
    """Reject a self-asserted vendor label whose captured host is not pinned."""

    if not isinstance(vendor, str) or not isinstance(url, str):
        raise ValueError("first-party vendor provenance needs a vendor name and HTTPS URL")
    allowed_hosts = _VENDOR_HOSTS.get(vendor.casefold())
    parsed = urlparse(url)
    host = parsed.hostname.casefold() if parsed.hostname else None
    if (
        allowed_hosts is None
        or parsed.scheme.casefold() != "https"
        or host not in allowed_hosts
    ):
        raise ValueError(
            "first-party vendor source is not on a pinned official host: " f"{vendor!r}"
        )

