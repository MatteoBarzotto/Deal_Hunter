"""GPT-powered offer evaluation via Azure OpenAI Responses API.

Newer models (GPT-5 family) only support the Responses API, not chat.completions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger
from openai import AzureOpenAI

from config import get_secrets, get_settings

_SYSTEM_PROMPT = """Jesteś ekspertem od oceny ofert na polskich platformach \
sprzedażowych (Allegro, OLX). Oceniasz oferty pod kątem:
- czy oferta wygląda na uczciwą (nie scam),
- czy cena jest atrakcyjna względem mediany kategorii,
- jaki jest prawdopodobny stan przedmiotu na podstawie opisu,
- czy warto kupić.

ZAWSZE odpowiadasz prawidłowym JSON-em o strukturze:
{
  "score": <1-10>,
  "is_scam_risk": <true|false>,
  "scam_indicators": [<lista>],
  "condition_assessment": "<ocena stanu>",
  "summary_pl": "<2-3 zdania po polsku>",
  "recommended_action": "<buy|watch|skip>"
}
Bez żadnego tekstu poza JSON-em."""


@dataclass
class AiAnalysis:
    score: int
    is_scam_risk: bool
    scam_indicators: list[str]
    condition_assessment: str
    summary_pl: str
    recommended_action: str
    raw: dict[str, Any]


def _build_client() -> AzureOpenAI:
    secrets = get_secrets()
    if not (secrets.azure_openai_endpoint and secrets.azure_openai_api_key):
        raise RuntimeError(
            "Azure OpenAI not configured — set AZURE_OPENAI_ENDPOINT and "
            "AZURE_OPENAI_API_KEY in config/.env"
        )
    logger.info(
        "AI: using Azure OpenAI Responses API (endpoint={}, deployment={})",
        secrets.azure_openai_endpoint,
        secrets.azure_openai_deployment,
    )
    return AzureOpenAI(
        api_key=secrets.azure_openai_api_key,
        azure_endpoint=secrets.azure_openai_endpoint,
        api_version=secrets.azure_openai_api_version,
    )


class AiAnalyzer:
    def __init__(self, client: AzureOpenAI | None = None) -> None:
        secrets = get_secrets()
        settings = get_settings()
        self._client = client if client is not None else _build_client()
        self._deployment = secrets.azure_openai_deployment or settings.ai_model
        self._max_tokens = settings.ai_max_tokens

    def analyze(
        self,
        *,
        title: str,
        price_pln: float,
        median_price_pln: float | None,
        description: str | None,
        platform: str,
        seller_info: str | None,
    ) -> AiAnalysis:
        user_prompt = self._build_user_prompt(
            title=title,
            price_pln=price_pln,
            median_price_pln=median_price_pln,
            description=description,
            platform=platform,
            seller_info=seller_info,
        )

        response = self._client.responses.create(
            model=self._deployment,
            instructions=_SYSTEM_PROMPT,
            input=user_prompt,
            text={"format": {"type": "json_object"}},
            max_output_tokens=self._max_tokens,
        )

        text = self._extract_text(response)
        return self._parse_response(text)

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Get the response text. Responses API exposes `output_text` convenience."""
        text = getattr(response, "output_text", None)
        if text:
            return text
        # Fallback: walk response.output[*].content[*].text
        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            for block in getattr(item, "content", []) or []:
                block_text = getattr(block, "text", None)
                if block_text:
                    parts.append(block_text)
        return "".join(parts)

    @staticmethod
    def _build_user_prompt(
        *,
        title: str,
        price_pln: float,
        median_price_pln: float | None,
        description: str | None,
        platform: str,
        seller_info: str | None,
    ) -> str:
        median_str = (
            f"{median_price_pln:.0f} PLN" if median_price_pln is not None else "brak danych"
        )
        return (
            "Oceń tę ofertę i zwróć WYŁĄCZNIE JSON.\n"
            f"- Tytuł: {title}\n"
            f"- Cena: {price_pln:.0f} PLN\n"
            f"- Mediana ceny w tej kategorii: {median_str}\n"
            f"- Opis: {description or '(brak)'}\n"
            f"- Platforma: {platform}\n"
            f"- Sprzedawca / lokalizacja: {seller_info or '(brak)'}\n"
        )

    @staticmethod
    def _parse_response(text: str) -> AiAnalysis:
        text = text.strip()
        # Some models occasionally wrap JSON in markdown fences — strip them.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("AI returned non-JSON response: {!r}", text[:200])
            raise ValueError(f"AI response was not valid JSON: {e}") from e

        score = int(data.get("score", 0))
        score = max(1, min(10, score))
        return AiAnalysis(
            score=score,
            is_scam_risk=bool(data.get("is_scam_risk", False)),
            scam_indicators=list(data.get("scam_indicators", [])),
            condition_assessment=str(data.get("condition_assessment", "")),
            summary_pl=str(data.get("summary_pl", "")),
            recommended_action=str(data.get("recommended_action", "watch")).lower(),
            raw=data,
        )
