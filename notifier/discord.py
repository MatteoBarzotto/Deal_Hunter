"""Discord webhook notifier — rich embeds with offer details + AI summary."""
from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from analyzer.ai_analyzer import AiAnalysis
from config import get_secrets
from database.models import Offer


class DiscordNotifier:
    """Posts an embed for each alert. One webhook URL, fire-and-forget."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self._webhook_url = webhook_url or get_secrets().discord_webhook_url

    def send_alert(
        self,
        offer: Offer,
        analysis: AiAnalysis,
        median_price: float | None,
    ) -> bool:
        if not self._webhook_url:
            logger.warning("Discord webhook URL not configured — skipping alert")
            return False

        embed = self._build_embed(offer, analysis, median_price)
        try:
            resp = httpx.post(
                self._webhook_url,
                json={"embeds": [embed]},
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("Discord webhook post failed: {}", e)
            return False
        logger.info("Discord alert sent for offer {} (score={})", offer.id, analysis.score)
        return True

    @staticmethod
    def _build_embed(
        offer: Offer, analysis: AiAnalysis, median_price: float | None
    ) -> dict[str, Any]:
        color = (
            0xE74C3C if analysis.is_scam_risk
            else 0x2ECC71 if analysis.recommended_action == "buy"
            else 0xF1C40F
        )
        fields = [
            {"name": "Cena", "value": f"**{offer.price_pln:.0f} PLN**", "inline": True},
            {
                "name": "Mediana kategorii",
                "value": f"{median_price:.0f} PLN" if median_price else "brak",
                "inline": True,
            },
            {"name": "Score", "value": f"{analysis.score}/10", "inline": True},
            {"name": "Platforma", "value": offer.platform, "inline": True},
            {"name": "Kategoria", "value": offer.category, "inline": True},
            {
                "name": "Akcja",
                "value": analysis.recommended_action.upper(),
                "inline": True,
            },
        ]
        if analysis.scam_indicators:
            fields.append(
                {
                    "name": "⚠️ Sygnały podejrzanych elementów",
                    "value": "\n".join(f"• {s}" for s in analysis.scam_indicators[:5]),
                    "inline": False,
                }
            )
        if analysis.condition_assessment:
            fields.append(
                {
                    "name": "Stan przedmiotu",
                    "value": analysis.condition_assessment[:1000],
                    "inline": False,
                }
            )

        embed: dict[str, Any] = {
            "title": offer.title[:256],
            "url": offer.url,
            "description": analysis.summary_pl[:2000],
            "color": color,
            "fields": fields,
        }
        if offer.image_url and offer.image_url.startswith(("http://", "https://")):
            embed["thumbnail"] = {"url": offer.image_url}
        if offer.seller_info:
            embed["footer"] = {"text": offer.seller_info[:200]}
        return embed
