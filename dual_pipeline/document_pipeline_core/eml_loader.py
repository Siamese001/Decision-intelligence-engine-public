"""RFC-822 EML document content loader and boilerplate sanitizer."""
from __future__ import annotations

import email
import email.policy
import re
from email.header import decode_header, make_header
from email.message import Message
from typing import Any, Optional


def _decode_bytes_safely(data: bytes, charset: Optional[str] = None) -> str:
    """Decode raw bytes respecting explicit charset, trying utf-8-sig, utf-16, falling back to latin-1."""
    if charset:
        try:
            return data.decode(charset)
        except Exception:
            pass
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except Exception:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except Exception:
            return data.decode("utf-8", errors="replace")


def _decode_header_val(raw_val: Any) -> str:
    if not raw_val:
        return ""
    s = str(raw_val).strip()
    if "=?" in s:
        try:
            return str(make_header(decode_header(s))).strip()
        except Exception:
            return s
    return s


def format_eml_headers(msg: Message) -> str:
    """Format standard RFC-822 email headers including Cc."""
    header_fields = ("Message-ID", "In-Reply-To", "References", "From", "To", "Cc", "Date", "Subject")
    lines = [
        f"{field}: {_decode_header_val(val)}"
        for field in header_fields
        if (val := msg.get(field, ""))
    ]
    return "\n".join(lines)


def extract_part_text(part: Message) -> Optional[str]:
    """Extract plain text or HTML markdown from a single MIME part."""
    ctype = part.get_content_type()
    payload = part.get_payload(decode=True)
    if not payload:
        return None
    charset = part.get_content_charset()

    if ctype == "text/plain":
        return _decode_bytes_safely(payload, charset=charset)
    if ctype == "text/html":
        try:
            from dual_pipeline.html_source import extract_html_markdown
            return extract_html_markdown(payload)
        except Exception:
            return _decode_bytes_safely(payload, charset=charset)
    return None


def clean_eml_boilerplate(text: str) -> str:
    """Strip tracking links, unsubscribe footers, and email marketing disclaimers."""
    if not text:
        return ""
    patterns = [
        r"(?i)<https?://[^>]+(?:unsubscribe|subscription_center|preference)[^>]*>",
        r"(?i)Update your email preferences or unsubscribe.*",
        r"(?i)PartnerAdvisory processes your data in accordance with our Privacy Policy.*",
        r"(?i)For questions, please email privacy@partner_advisory\.com.*",
        r"(?i)mailto:privacy@partner_advisory\.com",
        r"(?i)https?://internal\.partner_advisory\.com/(?:employee-subscriptions|email-preference-center)[^\s]+",
        r"(?i)https?://[^\s]*(?:emltrk\.com|exct\.net|pd25\.com)[^\s]*",
    ]
    for p in patterns:
        text = re.sub(p, "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def select_best_eml_body(body_parts: list[Message]) -> str:
    """Select preferred substantive plain text over wrapped marketing HTML layout templates."""
    plain_part = next((p for p in body_parts if p.get_content_type() == "text/plain"), None)
    plain_text = (extract_part_text(plain_part) or "").strip() if plain_part else ""

    html_part = next((p for p in body_parts if p.get_content_type() == "text/html"), None)
    html_text = (extract_part_text(html_part) or "").strip() if html_part else ""

    if plain_text and (len(plain_text) >= 100 or not html_text):
        return clean_eml_boilerplate(plain_text)
    if html_text:
        return clean_eml_boilerplate(html_text)
    return ""


def extract_eml_body_and_attachments(msg: Message) -> tuple[str, list[str]]:
    """Extract message body and attachment manifest across multipart or single part MIME structures."""
    if not msg.is_multipart():
        payload = msg.get_payload(decode=True)
        raw_txt = _decode_bytes_safely(payload, charset=msg.get_content_charset()) if payload else ""
        return clean_eml_boilerplate(raw_txt), []

    attachments: list[str] = []
    body_parts: list[Message] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if filename:
            attachments.append(_decode_header_val(filename))
        else:
            body_parts.append(part)

    body_text = select_best_eml_body(body_parts)
    return body_text, attachments


def extract_eml_markdown(data: bytes) -> str:
    """Extract standard RFC-822 email headers and body as canonical Markdown."""
    msg = email.message_from_bytes(data, policy=email.policy.default)
    header_block = format_eml_headers(msg)
    body_text, attachments = extract_eml_body_and_attachments(msg)

    parts = []
    if header_block:
        parts.append(header_block)
    if attachments:
        parts.append("Attachments: " + ", ".join(attachments))
    if body_text:
        parts.append(body_text)

    if parts:
        return "\n\n".join(parts)
    return _decode_bytes_safely(data)
