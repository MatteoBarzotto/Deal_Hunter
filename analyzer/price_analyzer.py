"""Price analyzer: compares an offer's price against the category's historical median."""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import PriceHistory


@dataclass
class PriceAnalysis:
    median_price: float | None
    samples: int
    is_deal: bool
    drop_pct: float | None  # fraction below median, e.g. 0.35 = 35% cheaper
    reason: str


class PriceAnalyzer:
    """Stateless: each instance just carries config."""

    def __init__(self, drop_threshold_pct: float, min_samples: int) -> None:
        self._threshold = drop_threshold_pct
        self._min_samples = min_samples

    def record(
        self, session: Session, category: str, platform: str, price_pln: float
    ) -> None:
        """Append a price sample. Caller commits."""
        session.add(
            PriceHistory(category=category, platform=platform, price_pln=price_pln)
        )

    def median_for_category(self, session: Session, category: str) -> tuple[float | None, int]:
        rows = session.execute(
            select(PriceHistory.price_pln).where(PriceHistory.category == category)
        ).scalars().all()
        if not rows:
            return None, 0
        return statistics.median(rows), len(rows)

    def evaluate(
        self, session: Session, category: str, price_pln: float
    ) -> PriceAnalysis:
        median, samples = self.median_for_category(session, category)
        if median is None or samples < self._min_samples:
            return PriceAnalysis(
                median_price=median,
                samples=samples,
                is_deal=False,
                drop_pct=None,
                reason=f"not enough history ({samples}/{self._min_samples})",
            )
        if price_pln <= 0:
            return PriceAnalysis(median, samples, False, None, "non-positive price")
        drop_pct = (median - price_pln) / median
        is_deal = drop_pct >= self._threshold
        reason = (
            f"price {price_pln:.0f} is {drop_pct * 100:.1f}% below median {median:.0f}"
            if is_deal
            else f"price {price_pln:.0f} only {drop_pct * 100:.1f}% below median {median:.0f}"
        )
        return PriceAnalysis(median, samples, is_deal, drop_pct, reason)
