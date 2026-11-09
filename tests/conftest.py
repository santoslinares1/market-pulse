from datetime import datetime, timezone, timedelta
import pytest
from marketpulse.normalize import Item

@pytest.fixture
def now():
    return datetime(2026, 11, 8, 22, tzinfo=timezone.utc)

@pytest.fixture
def make_item(now):
    def factory(hours=1, score=.6, source="yahoo", ticker="AAPL", text="Apple raises guidance", id="1"):
        return Item(ticker, source, (now - timedelta(hours=hours)).isoformat(),
                    f"https://example.com/{id}", text,
                    "bullish" if score >= .2 else "bearish" if score <= -.2 else "neutral", score)
    return factory
