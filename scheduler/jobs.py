"""Scan pipeline + APScheduler wiring.

Pipeline per scan tick:
  1. Each scraper produces ScrapedOffer list.
  2. Deduplicate against DB (platform + external_id), persist new ones.
  3. Update price history for every offer.
  4. Price analyzer flags potential deals.
  5. Claude analyzes flagged offers.
  6. Discord notifier sends alerts above the score threshold.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from sqlalchemy import select

from analyzer.ai_analyzer import AiAnalyzer
from analyzer.price_analyzer import PriceAnalyzer
from config import get_settings
from database.db import session_scope
from database.models import Alert, Offer
from notifier.discord import DiscordNotifier
from scrapers.allegro import AllegroScraper
from scrapers.base import BaseScraper, ScrapedOffer
from scrapers.olx import OlxScraper


def _build_scrapers() -> list[BaseScraper]:
    return [AllegroScraper(), OlxScraper()]


async def run_scan_cycle() -> None:
    """Single end-to-end scan tick."""
    settings = get_settings()
    scrapers = _build_scrapers()
    price_analyzer = PriceAnalyzer(
        drop_threshold_pct=settings.price_drop_threshold_pct,
        min_samples=settings.min_history_samples,
    )

    logger.info("=== Starting scan cycle ===")

    all_offers: list[ScrapedOffer] = []
    for scraper in scrapers:
        try:
            offers = await scraper.scan()
            all_offers.extend(offers)
        except Exception as e:  # noqa: BLE001
            logger.exception("Scraper {} failed: {}", scraper.platform, e)

    if not all_offers:
        logger.info("No offers scraped; cycle done.")
        return

    new_offers, flagged = _persist_and_flag(all_offers, price_analyzer)
    logger.info(
        "Persisted {} new offers, {} flagged as potential deals",
        len(new_offers),
        len(flagged),
    )

    if not flagged:
        return

    await _evaluate_and_notify(flagged, settings.ai_score_threshold)
    logger.info("=== Scan cycle complete ===")


def _persist_and_flag(
    scraped: list[ScrapedOffer], price_analyzer: PriceAnalyzer
) -> tuple[list[int], list[tuple[int, float | None]]]:
    """Persist offers (skipping duplicates) and return ids of price-flagged ones.

    Returns (new_offer_ids, flagged_pairs) where each flagged pair is
    (offer_id, median_price).
    """
    new_ids: list[int] = []
    flagged: list[tuple[int, float | None]] = []

    with session_scope() as session:
        for s in scraped:
            existing = session.execute(
                select(Offer).where(
                    Offer.platform == s.platform,
                    Offer.external_id == s.external_id,
                )
            ).scalar_one_or_none()

            if existing is not None:
                price_analyzer.record(session, s.category, s.platform, s.price_pln)
                continue

            offer = Offer(
                platform=s.platform,
                external_id=s.external_id,
                url=s.url,
                title=s.title,
                description=s.description,
                price_pln=s.price_pln,
                category=s.category,
                seller_info=s.seller_info,
                image_url=s.image_url,
                posted_at=s.posted_at,
                raw_data=s.raw_data,
            )
            session.add(offer)
            session.flush()  # populate offer.id

            price_analyzer.record(session, s.category, s.platform, s.price_pln)
            analysis = price_analyzer.evaluate(session, s.category, s.price_pln)
            logger.debug(
                "Offer {} ({}): {}", offer.id, s.title[:50], analysis.reason
            )
            new_ids.append(offer.id)
            if analysis.is_deal:
                flagged.append((offer.id, analysis.median_price))

    return new_ids, flagged


async def _evaluate_and_notify(
    flagged: list[tuple[int, float | None]], score_threshold: int
) -> None:
    """Run AI analysis on flagged offers and dispatch alerts above threshold."""
    ai = AiAnalyzer()
    notifier = DiscordNotifier()

    loop = asyncio.get_running_loop()
    settings = get_settings()
    freshness_cutoff = datetime.utcnow() - timedelta(hours=settings.offer_freshness_hours)

    for offer_id, median in flagged:
        with session_scope() as session:
            offer = session.get(Offer, offer_id)
            if offer is None:
                continue
            # Skip if we've already alerted on this offer recently.
            existing_alert = session.execute(
                select(Alert).where(Alert.offer_id == offer_id)
            ).scalar_one_or_none()
            if existing_alert is not None:
                continue
            if offer.scraped_at < freshness_cutoff:
                continue

            try:
                analysis = await loop.run_in_executor(
                    None,
                    lambda: ai.analyze(
                        title=offer.title,
                        price_pln=offer.price_pln,
                        median_price_pln=median,
                        description=offer.description,
                        platform=offer.platform,
                        seller_info=offer.seller_info,
                    ),
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("AI analysis failed for offer {}: {}", offer_id, e)
                continue

            alert = Alert(
                offer_id=offer.id,
                score=analysis.score,
                is_scam_risk=analysis.is_scam_risk,
                recommended_action=analysis.recommended_action,
                summary_pl=analysis.summary_pl,
                median_price_pln=median,
                ai_response=analysis.raw,
            )
            session.add(alert)
            session.flush()

            if analysis.score >= score_threshold and not analysis.is_scam_risk:
                if notifier.send_alert(offer, analysis, median):
                    alert.sent_at = datetime.utcnow()
            else:
                logger.info(
                    "Offer {} score={} below threshold={} (or scam risk={}) — alert recorded but not sent",
                    offer.id,
                    analysis.score,
                    score_threshold,
                    analysis.is_scam_risk,
                )


def create_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_scan_cycle,
        trigger="interval",
        minutes=settings.scan_interval_minutes,
        next_run_time=datetime.utcnow() + timedelta(seconds=10),
        max_instances=1,
        coalesce=True,
        id="scan_cycle",
    )
    logger.info(
        "Scheduler configured: scan every {} minutes", settings.scan_interval_minutes
    )
    return scheduler
