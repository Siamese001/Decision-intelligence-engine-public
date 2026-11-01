"""Unit tests for dual_pipeline.attachment_core.image_extractor model selection and extraction."""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.attachment_core.image_extractor import (
    _extract_svg_diagram,
    _extract_via_gemini_vision,
    extract_image_visual,
)
from dual_pipeline.model_registry import ModelRegistry, ModelTier


def test_gemini_vision_model_is_pinned_to_gemini_3_8_flash():
    """Verify that Gemini vision model tier resolves to gemini-3.8-flash."""
    model_name, tier_strength = ModelRegistry.resolve_tier_model(ModelTier.ANTIGRAVITY_AGENT_FLASH)
    assert model_name == "gemini-3.8-flash"
    src = inspect.getsource(_extract_via_gemini_vision)
    assert "gemini-2.5-flash" not in src  # allow-retired-model


def test_extract_svg_diagram_returns_valid_structure():
    """Verify deterministic SVG diagram parsing produces valid topology dictionary."""
    svg_bytes = b"<svg><text>Service A</text><text>Service B</text><line x1='0' y1='0' x2='1' y2='1'/></svg>"
    res = _extract_svg_diagram(svg_bytes)
    assert isinstance(res, dict)
    assert "text" in res or "components" in res
    assert "components" in res
    assert "connections" in res
    assert len(res["components"]) >= 1


def test_extract_image_visual_mock_mode_prohibited(tmp_path: Path):
    """Verify extract_image_visual fails-closed when mock_mode is attempted."""
    dummy_img = tmp_path / "payment_gateway.png"
    dummy_img.write_bytes(b"dummy")

    with pytest.raises(RuntimeError, match="MOCK_OCR_PROHIBITED"):
        extract_image_visual(dummy_img, mock_mode=True)


def test_extract_image_visual_svg(tmp_path: Path):
    """Verify SVG diagram routing."""
    svg_file = tmp_path / "arch.svg"
    svg_file.write_bytes(b"<svg><text>Service A</text></svg>")

    res = extract_image_visual(svg_file, mock_mode=False)
    assert "components" in res


def test_extract_via_gemini_vision_mocked(tmp_path: Path):
    """Verify _extract_via_gemini_vision handles client generation cleanly."""
    dummy_img = tmp_path / "slide1.png"
    dummy_img.write_bytes(b"dummy")

    fake_resp = MagicMock()
    fake_resp.text = "Slide 1: Executive Overview\nComponent: Auth Service\nComponent: Gateway"

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = fake_resp

    mock_gcreds = MagicMock()
    mock_gcreds.is_mock = False
    mock_gcreds.api_key = "fake-key"

    with patch("config.credentials.resolve_google_credentials", return_value=mock_gcreds), \
         patch("google.genai.Client", return_value=mock_client), \
         patch("PIL.Image.open", return_value=MagicMock()):
        result = _extract_via_gemini_vision(dummy_img)
        assert result is not None
        assert "Executive Overview" in result["summary"]
        assert len(result["components"]) >= 1
