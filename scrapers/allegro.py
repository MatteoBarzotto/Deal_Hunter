"""Allegro REST API client using a user-delegated token (device-code flow).

Run `python auth_allegro.py` once to authorize; subsequent runs auto-refresh via
the saved refresh_token. See: https://developer.allegro.pl/auth/#device-flow
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import get_secrets, get_settings

from .allegro_auth import get_valid_token
from .base import BaseScraper, ScrapedOffer

_API_BASE = "https://api.allegro.pl"
_API_HEADER = "application/vnd.allegro.public.v1+json"


class AllegroScraper(BaseScraper):
    platform = "allegro"

    def __init__(self) -> None:
        self._settings = get_settings().allegro
        self._user_agent = get_secrets().allegro_user_agent

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError,)),
        reraise=True,
    )
    async def _fetch_listing(
        self,
        client: httpx.AsyncClient,
        category_id: str,
        max_price: float | None,
        limit: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "category.id": category_id,
            "limit": min(limit, 60),
            "sort": "-startTime",
        }
        if max_price is not None:
            params["price.lte"] = str(max_price)
        token = get_valid_token()
        resp = await client.get(
            f"{_API_BASE}/offers/listing",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": _API_HEADER,
                "Accept-Language": "pl-PL",
                "User-Agent": self._user_agent,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def scan(self) -> list[ScrapedOffer]:
        if not self._settings.enabled:
            return []

        try:
            get_valid_token()
        except RuntimeError as e:
            logger.warning("Allegro skipped: {}", e)
            return []

        offers: list[ScrapedOffer] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for cat in self._settings.categories:
                if not cat.id:
                    continue
                try:
                    data = await self._fetch_listing(
                        client, cat.id, cat.max_price_pln, self._settings.limit_per_scan
                    )
                except httpx.HTTPStatusError as e:
                    logger.error(
                        "Allegro listing fetch failed for {}: {} — body: {}",
                        cat.name,
                        e,
                        e.response.text[:500],
                    )
                    continue
                except httpx.HTTPError as e:
                    logger.error("Allegro listing fetch failed for {}: {}", cat.name, e)
                    continue

                items = (data.get("items") or {}).get("regular", []) + (
                    data.get("items") or {}
                ).get("promoted", [])
                for item in items:
                    parsed = self._parse_item(item, cat.name)
                    if parsed is not None:
                        offers.append(parsed)

                await asyncio.sleep(0.3)

        logger.info("Allegro: scraped {} offers", len(offers))
        return offers

    def _parse_item(self, item: dict[str, Any], category_name: str) -> ScrapedOffer | None:
        try:
            external_id = str(item["id"])
            title = item["name"]
            price_data = item.get("sellingMode", {}).get("price", {})
            price = float(price_data.get("amount", 0))
            if price <= 0:
                return None
            seller = item.get("seller", {})
            seller_info = seller.get("login")
            images = item.get("images", [])
            image_url = images[0].get("url") if images else None
            return ScrapedOffer(
                platform=self.platform,
                external_id=external_id,
                url=f"https://allegro.pl/oferta/{external_id}",
                title=title,
                price_pln=price,
                category=category_name,
                seller_info=seller_info,
                image_url=image_url,
                posted_at=datetime.utcnow(),
                raw_data={
                    "publication": item.get("publication"),
                    "stock": item.get("stock"),
                    "seller": seller,
                },
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Failed to parse Allegro item: {}", e)
            return None
