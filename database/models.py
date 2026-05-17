"""SQLAlchemy ORM models for offers, price history and alerts."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Offer(Base):
    """One listing scraped from a platform."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_pln: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(128), index=True)
    seller_info: Mapped[str | None] = mapped_column(String(256), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    alert: Mapped["Alert | None"] = relationship(back_populates="offer", uselist=False)

    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_platform_external_id"),
        Index("ix_category_scraped_at", "category", "scraped_at"),
    )


class PriceHistory(Base):
    """Aggregated price samples per category, used for median computation."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(128), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    price_pln: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )


class WatchCategory(Base):
    """User-configurable category to monitor. Managed via dashboard, persisted in DB."""

    __tablename__ = "watch_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), default="olx")
    name: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(String(1024))
    min_price_pln: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_price_pln: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Comma-separated phrases. include: title must contain at least one (OR).
    # exclude: title must contain none. Both case-insensitive substring match.
    include_keywords: Mapped[str | None] = mapped_column(String(512), nullable=True)
    exclude_keywords: Mapped[str | None] = mapped_column(String(512), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Alert(Base):
    """Alert dispatched (or queued) for an offer."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), unique=True
    )
    score: Mapped[int] = mapped_column(Integer)
    is_scam_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    recommended_action: Mapped[str] = mapped_column(String(16))
    summary_pl: Mapped[str] = mapped_column(Text)
    median_price_pln: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    offer: Mapped[Offer] = relationship(back_populates="alert")
