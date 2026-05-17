"""AI analyzer tests — mock AzureOpenAI client; never hit real API in CI."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from analyzer.ai_analyzer import AiAnalyzer


def _mock_client(response_text: str) -> MagicMock:
    """Build a fake AzureOpenAI returning `response_text` via the Responses API."""
    response = MagicMock()
    response.output_text = response_text
    client = MagicMock()
    client.responses.create.return_value = response
    return client


def test_parses_valid_json_response():
    payload = {
        "score": 8,
        "is_scam_risk": False,
        "scam_indicators": [],
        "condition_assessment": "Stan bardzo dobry",
        "summary_pl": "Cena 40% poniżej mediany, sprzedawca z dobrą historią.",
        "recommended_action": "buy",
    }
    analyzer = AiAnalyzer(client=_mock_client(json.dumps(payload)))
    result = analyzer.analyze(
        title="iPhone 13",
        price_pln=2000,
        median_price_pln=3500,
        description="Stan idealny, gwarancja",
        platform="allegro",
        seller_info="testowy_sprzedawca",
    )
    assert result.score == 8
    assert result.is_scam_risk is False
    assert result.recommended_action == "buy"
    assert "40%" in result.summary_pl


def test_strips_markdown_fences():
    payload = {
        "score": 3,
        "is_scam_risk": True,
        "scam_indicators": ["zaliczka", "brak telefonu"],
        "condition_assessment": "Nieznany",
        "summary_pl": "Wygląda na próbę oszustwa.",
        "recommended_action": "skip",
    }
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    analyzer = AiAnalyzer(client=_mock_client(fenced))
    result = analyzer.analyze(
        title="iPhone 15 PRO MAX 256GB",
        price_pln=400,
        median_price_pln=5500,
        description="zadatek 100zł, kontakt tylko mail",
        platform="olx",
        seller_info=None,
    )
    assert result.is_scam_risk is True
    assert result.recommended_action == "skip"
    assert "zaliczka" in result.scam_indicators


def test_clamps_score_range():
    payload = {
        "score": 99,
        "is_scam_risk": False,
        "scam_indicators": [],
        "condition_assessment": "",
        "summary_pl": "ok",
        "recommended_action": "buy",
    }
    analyzer = AiAnalyzer(client=_mock_client(json.dumps(payload)))
    result = analyzer.analyze(
        title="x",
        price_pln=1,
        median_price_pln=2,
        description=None,
        platform="allegro",
        seller_info=None,
    )
    assert result.score == 10


def test_raises_on_invalid_json():
    analyzer = AiAnalyzer(client=_mock_client("totally not json"))
    with pytest.raises(ValueError):
        analyzer.analyze(
            title="x",
            price_pln=1,
            median_price_pln=2,
            description=None,
            platform="allegro",
            seller_info=None,
        )
