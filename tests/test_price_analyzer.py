from __future__ import annotations

from analyzer.price_analyzer import PriceAnalyzer


def test_not_enough_history(db_session):
    analyzer = PriceAnalyzer(drop_threshold_pct=0.30, min_samples=5)
    result = analyzer.evaluate(db_session, "Smartphones", 1000.0)
    assert result.is_deal is False
    assert result.samples == 0
    assert result.median_price is None


def test_below_threshold_not_a_deal(db_session):
    analyzer = PriceAnalyzer(drop_threshold_pct=0.30, min_samples=3)
    for price in [1000, 1100, 1050, 1200, 950]:
        analyzer.record(db_session, "Smartphones", "allegro", float(price))
    db_session.commit()

    # 10% below median (1050) — should not flag
    result = analyzer.evaluate(db_session, "Smartphones", 945.0)
    assert result.is_deal is False
    assert result.median_price == 1050.0
    assert result.samples == 5


def test_above_threshold_is_a_deal(db_session):
    analyzer = PriceAnalyzer(drop_threshold_pct=0.30, min_samples=3)
    for price in [1000, 1100, 1050, 1200, 950]:
        analyzer.record(db_session, "Smartphones", "allegro", float(price))
    db_session.commit()

    # 50% below median (1050) — clear deal
    result = analyzer.evaluate(db_session, "Smartphones", 500.0)
    assert result.is_deal is True
    assert result.drop_pct is not None
    assert result.drop_pct > 0.30


def test_zero_or_negative_price_rejected(db_session):
    analyzer = PriceAnalyzer(drop_threshold_pct=0.30, min_samples=2)
    for price in [1000, 1100, 1050]:
        analyzer.record(db_session, "Smartphones", "allegro", float(price))
    db_session.commit()
    result = analyzer.evaluate(db_session, "Smartphones", 0.0)
    assert result.is_deal is False


def test_median_isolated_per_category(db_session):
    analyzer = PriceAnalyzer(drop_threshold_pct=0.30, min_samples=2)
    for price in [1000, 1100, 1050]:
        analyzer.record(db_session, "Phones", "allegro", float(price))
    for price in [5000, 5500, 5200]:
        analyzer.record(db_session, "Consoles", "allegro", float(price))
    db_session.commit()

    phones = analyzer.median_for_category(db_session, "Phones")
    consoles = analyzer.median_for_category(db_session, "Consoles")
    assert phones[0] == 1050.0
    assert consoles[0] == 5200.0
