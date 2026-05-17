"""OLX listing scraper.

OLX listing pages are server-side rendered with JSON-LD metadata,
so httpx + BeautifulSoup is enough — no Playwright needed at this stage.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from sqlalchemy import select

from config import get_settings
from database.db import session_scope
from database.models import WatchCategory

from .base import BaseScraper, ScrapedOffer

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_PRICE_RE = re.compile(r"(\d[\d\s.,]*)")


def _parse_keywords(raw: str | None) -> list[str]:
    """Comma-separated phrases → list of lowercase trimmed strings."""
    if not raw:
        return []
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def _title_matches(title: str, include: list[str], exclude: list[str]) -> bool:
    """OR-include + OR-exclude semantics, case-insensitive substring match."""
    t = title.lower()
    if include and not any(k in t for k in include):
        return False
    if exclude and any(k in t for k in exclude):
        return False
    return True


class OlxScraper(BaseScraper):
    platform = "olx"

    def __init__(self) -> None:
        self._settings = get_settings().olx

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=15),
        retry=retry_if_exception_type((httpx.HTTPError,)),
        reraise=True,
    )
    async def _fetch_page(self, client: httpx.AsyncClient, url: str) -> str:
        resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        return resp.text

    async def scan(self) -> list[ScrapedOffer]:
        if not self._settings.enabled:
            return []

        # Categories are stored in DB and managed via the dashboard.
        with session_scope() as session:
            rows = session.execute(
                select(WatchCategory).where(
                    WatchCategory.platform == "olx", WatchCategory.enabled.is_(True)
                )
            ).scalars().all()
            categories = [
                (
                    c.url,
                    c.name,
                    c.max_price_pln,
                    c.min_price_pln,
                    _parse_keywords(c.include_keywords),
                    _parse_keywords(c.exclude_keywords),
                )
                for c in rows
            ]

        if not categories:
            logger.info("OLX: no enabled categories — nothing to scan")
            return []

        offers: list[ScrapedOffer] = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for url, name, max_price, min_price, include, exclude in categories:
                try:
                    html = await self._fetch_page(client, url)
                except httpx.HTTPError as e:
                    logger.error("OLX page fetch failed for {}: {}", name, e)
                    continue
                parsed = self._parse_listing(html, url, name, max_price, min_price)
                filtered = [o for o in parsed if _title_matches(o.title, include, exclude)]
                skipped = len(parsed) - len(filtered)
                if skipped:
                    logger.info(
                        "OLX[{}]: keyword filter removed {}/{} offers (include={}, exclude={})",
                        name, skipped, len(parsed), include or "—", exclude or "—",
                    )
                    if not filtered and parsed:
                        sample = [o.title for o in parsed[:5]]
                        logger.warning(
                            "OLX[{}]: filter rejected ALL offers. Sample titles you're filtering out:\n  - {}",
                            name, "\n  - ".join(sample),
                        )
                offers.extend(filtered[: self._settings.limit_per_scan])
                await asyncio.sleep(1.0)  # rate-limit to be polite

        logger.info("OLX: scraped {} offers", len(offers))
        return offers

    def _parse_listing(
        self,
        html: str,
        base_url: str,
        category_name: str,
        max_price: float | None,
        min_price: float | None,
    ) -> list[ScrapedOffer]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[ScrapedOffer] = []

        cards = soup.select('div[data-cy="l-card"]')
        for card in cards:
            try:
                offer = self._parse_card(card, base_url, category_name)
            except Exception as e:  # noqa: BLE001 — never let one card break the batch
                logger.debug("OLX card parse skipped: {}", e)
                continue
            if offer is None:
                continue
            if max_price is not None and offer.price_pln > max_price:
                continue
            if min_price is not None and offer.price_pln < min_price:
                continue
            results.append(offer)
        return results

    def _parse_card(
        self, card, base_url: str, category_name: str
    ) -> ScrapedOffer | None:
        link = card.select_one("a")
        if not link or not link.get("href"):
            return None
        url = urljoin(base_url, link["href"])
        external_id = self._extract_id(url)
        if not external_id:
            return None

        title_el = card.select_one("h4, h6")
        title = title_el.get_text(strip=True) if title_el else None
        if not title:
            return None

        price_el = card.select_one('p[data-testid="ad-price"]')
        price = self._parse_price(price_el.get_text() if price_el else "")
        if price is None:
            return None

        image_url = self._extract_image_url(card)

        location_el = card.select_one('p[data-testid="location-date"]')
        location_date = location_el.get_text(strip=True) if location_el else None

        return ScrapedOffer(
            platform=self.platform,
            external_id=external_id,
            url=url,
            title=title,
            price_pln=price,
            category=category_name,
            description=None,
            seller_info=location_date,
            image_url=image_url,
            posted_at=datetime.utcnow(),
            raw_data={"location_date": location_date},
        )

    _IMG_URL_RE = re.compile(r'https?://[a-z0-9.\-:]*olxcdn\.com[^\s"\'>\\]+')

    @classmethod
    def _extract_image_url(cls, card) -> str | None:
        """Find the first absolute OLX-CDN image URL anywhere in the card HTML.

        OLX uses lazy loading, picture/source elements, srcset and webp variants;
        a plain `img.src` is often just a placeholder. A regex over the rendered
        HTML reliably catches the real CDN URL regardless of the markup variant.
        """
        if card is None:
            return None
        match = cls._IMG_URL_RE.search(str(card))
        return match.group(0) if match else None

    @staticmethod
    def _extract_id(url: str) -> str | None:
        # OLX URLs end with -ID<alphanum>.html  e.g. .../oferta-IDx9aB2.html
        match = re.search(r"-ID([A-Za-z0-9]+)\.html", url)
        if match:
            return match.group(1)
        # Fallback: last path segment
        return url.rstrip("/").split("/")[-1][:128]

    @staticmethod
    def _parse_price(raw: str) -> float | None:
        if not raw:
            return None
        if "zamienię" in raw.lower() or "darmo" in raw.lower():
            return None
        match = _PRICE_RE.search(raw)
        if not match:
            return None
        token = match.group(1).replace(" ", "").replace("\xa0", "")
        # Polish formatting: 1.299,00 or 1299,00 or 1299
        if "," in token and "." in token:
            token = token.replace(".", "").replace(",", ".")
        elif "," in token:
            token = token.replace(",", ".")
        try:
            return float(token)
        except ValueError:
            return None
