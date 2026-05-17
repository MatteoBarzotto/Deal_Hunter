"""Common types for scrapers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ScrapedOffer:
    """Normalized offer representation produced by any scraper."""

    platform: str
    external_id: str
    url: str
    title: str
    price_pln: float
    category: str
    description: str | None = None
    seller_info: str | None = None
    image_url: str | None = None
    posted_at: datetime | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


class BaseScraper(ABC):
    """Each platform-specific scraper returns a flat list of ScrapedOffer."""

    platform: str

    @abstractmethod
    async def scan(self) -> list[ScrapedOffer]:
        """Run a single scan cycle across all configured categories."""
        raise NotImplementedError
