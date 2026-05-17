"""FastAPI routes: JSON API + Jinja2 dashboard."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from config import PROJECT_ROOT, get_settings
from database.db import get_session
from database.models import Alert, Offer, PriceHistory, WatchCategory

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/settings")
def settings_view() -> dict[str, Any]:
    s = get_settings()
    return s.model_dump()


@router.get("/api/offers")
def list_offers(
    limit: int = 50,
    platform: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    stmt = select(Offer).order_by(desc(Offer.scraped_at)).limit(limit)
    if platform:
        stmt = stmt.where(Offer.platform == platform)
    rows = session.execute(stmt).scalars().all()
    return [_offer_to_dict(o) for o in rows]


@router.get("/api/alerts")
def list_alerts(
    limit: int = 50, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    stmt = (
        select(Alert)
        .order_by(desc(Alert.created_at))
        .limit(limit)
    )
    rows = session.execute(stmt).scalars().all()
    return [_alert_to_dict(a) for a in rows]


class CategoryIn(BaseModel):
    name: str
    url: str
    min_price_pln: float | None = None
    max_price_pln: float | None = None
    include_keywords: str | None = None
    exclude_keywords: str | None = None
    enabled: bool = True


@router.get("/api/categories")
def list_categories(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = session.execute(
        select(WatchCategory).order_by(WatchCategory.id)
    ).scalars().all()
    return [_category_to_dict(c) for c in rows]


@router.post("/api/categories")
def create_category(
    payload: CategoryIn, session: Session = Depends(get_session)
) -> dict[str, Any]:
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    cat = WatchCategory(
        platform="olx",
        name=payload.name.strip(),
        url=payload.url.strip(),
        min_price_pln=payload.min_price_pln,
        max_price_pln=payload.max_price_pln,
        include_keywords=_clean(payload.include_keywords),
        exclude_keywords=_clean(payload.exclude_keywords),
        enabled=payload.enabled,
    )
    session.add(cat)
    session.commit()
    session.refresh(cat)
    return _category_to_dict(cat)


@router.post("/api/categories/{cat_id}")
def update_category(
    cat_id: int, payload: CategoryIn, session: Session = Depends(get_session)
) -> dict[str, Any]:
    cat = session.get(WatchCategory, cat_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="Category not found")
    cat.name = payload.name.strip()
    cat.url = payload.url.strip()
    cat.min_price_pln = payload.min_price_pln
    cat.max_price_pln = payload.max_price_pln
    cat.include_keywords = _clean(payload.include_keywords)
    cat.exclude_keywords = _clean(payload.exclude_keywords)
    cat.enabled = payload.enabled
    session.commit()
    session.refresh(cat)
    return _category_to_dict(cat)


@router.post("/api/categories/{cat_id}/toggle")
def toggle_category(
    cat_id: int, session: Session = Depends(get_session)
) -> dict[str, Any]:
    cat = session.get(WatchCategory, cat_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="Category not found")
    cat.enabled = not cat.enabled
    session.commit()
    session.refresh(cat)
    return _category_to_dict(cat)


@router.delete("/api/categories/{cat_id}")
def delete_category(
    cat_id: int, session: Session = Depends(get_session)
) -> dict[str, str]:
    cat = session.get(WatchCategory, cat_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="Category not found")
    session.delete(cat)
    session.commit()
    return {"status": "deleted"}


@router.get("/api/categories/{cat_id}/preview")
async def preview_category(
    cat_id: int, session: Session = Depends(get_session)
) -> dict[str, Any]:
    """Fetch raw titles from a category's URL and show which pass/fail the keyword filter.

    Useful for debugging when "filter rejects everything".
    """
    import httpx
    from bs4 import BeautifulSoup
    from scrapers.olx import OlxScraper, _parse_keywords, _title_matches

    cat = session.get(WatchCategory, cat_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="Category not found")

    scraper = OlxScraper()
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            html = await scraper._fetch_page(client, cat.url)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"OLX fetch failed: {e}")

    soup = BeautifulSoup(html, "html.parser")
    include = _parse_keywords(cat.include_keywords)
    exclude = _parse_keywords(cat.exclude_keywords)

    passed: list[str] = []
    rejected: list[str] = []
    for card in soup.select('div[data-cy="l-card"]')[:30]:
        title_el = card.select_one("h4, h6")
        title = title_el.get_text(strip=True) if title_el else None
        if not title:
            continue
        (passed if _title_matches(title, include, exclude) else rejected).append(title)

    return {
        "category": cat.name,
        "url": cat.url,
        "include": include,
        "exclude": exclude,
        "passed_count": len(passed),
        "rejected_count": len(rejected),
        "passed_sample": passed[:10],
        "rejected_sample": rejected[:10],
    }


@router.post("/api/scan-now")
async def scan_now() -> dict[str, str]:
    """Fire-and-forget: trigger a scan cycle on the running event loop."""
    # Import here to avoid circular import at module load.
    from scheduler.jobs import run_scan_cycle

    asyncio.create_task(run_scan_cycle())
    return {"status": "scan triggered"}


@router.get("/api/stats")
def stats(session: Session = Depends(get_session)) -> dict[str, Any]:
    offer_count = session.execute(select(func.count(Offer.id))).scalar_one()
    alert_count = session.execute(select(func.count(Alert.id))).scalar_one()
    history_count = session.execute(select(func.count(PriceHistory.id))).scalar_one()
    per_platform = session.execute(
        select(Offer.platform, func.count(Offer.id)).group_by(Offer.platform)
    ).all()
    return {
        "total_offers": offer_count,
        "total_alerts": alert_count,
        "price_samples": history_count,
        "offers_per_platform": {row[0]: row[1] for row in per_platform},
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    alerts = (
        session.execute(select(Alert).order_by(desc(Alert.created_at)).limit(20))
        .scalars()
        .all()
    )
    alert_view = []
    for a in alerts:
        offer = a.offer
        alert_view.append(
            {
                "id": a.id,
                "title": offer.title,
                "url": offer.url,
                "platform": offer.platform,
                "category": offer.category,
                "price": offer.price_pln,
                "median": a.median_price_pln,
                "score": a.score,
                "is_scam_risk": a.is_scam_risk,
                "action": a.recommended_action,
                "summary": a.summary_pl,
                "image_url": offer.image_url,
                "created_at": a.created_at,
            }
        )
    categories = session.execute(
        select(WatchCategory).order_by(WatchCategory.id)
    ).scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"alerts": alert_view, "categories": list(categories)},
    )


def _offer_to_dict(o: Offer) -> dict[str, Any]:
    return {
        "id": o.id,
        "platform": o.platform,
        "external_id": o.external_id,
        "url": o.url,
        "title": o.title,
        "price_pln": o.price_pln,
        "category": o.category,
        "seller_info": o.seller_info,
        "image_url": o.image_url,
        "scraped_at": o.scraped_at.isoformat() if o.scraped_at else None,
    }


def _category_to_dict(c: WatchCategory) -> dict[str, Any]:
    return {
        "id": c.id,
        "platform": c.platform,
        "name": c.name,
        "url": c.url,
        "min_price_pln": c.min_price_pln,
        "max_price_pln": c.max_price_pln,
        "include_keywords": c.include_keywords,
        "exclude_keywords": c.exclude_keywords,
        "enabled": c.enabled,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _clean(s: str | None) -> str | None:
    """Trim and return None for empty strings."""
    if s is None:
        return None
    s = s.strip()
    return s or None


def _alert_to_dict(a: Alert) -> dict[str, Any]:
    return {
        "id": a.id,
        "offer_id": a.offer_id,
        "score": a.score,
        "is_scam_risk": a.is_scam_risk,
        "recommended_action": a.recommended_action,
        "summary_pl": a.summary_pl,
        "median_price_pln": a.median_price_pln,
        "sent_at": a.sent_at.isoformat() if a.sent_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "offer": _offer_to_dict(a.offer),
    }
