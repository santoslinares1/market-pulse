from datetime import timedelta
from unittest.mock import Mock
import pytest
import requests
from marketpulse.sources import yahoo, reddit, x, USER_AGENT

RSS = b'''<rss><channel><item><title>Apple raises guidance</title><link>https://example.com/a</link><pubDate>Sun, 08 Nov 2026 21:00:00 GMT</pubDate></item><item><title>Other company news</title><link>https://example.com/b</link><pubDate>Sun, 08 Nov 2026 21:00:00 GMT</pubDate></item></channel></rss>'''


def test_rss_filter_and_fetch(now):
    session = Mock()
    session.get.return_value.content = RSS
    out = yahoo.fetch_news("AAPL", now, 24, session)
    assert len(out.items) == 1 and out.fetched == 2
    assert session.get.call_args.kwargs["params"]["s"] == "AAPL"
    assert session.get.call_args.kwargs["headers"]["User-Agent"] == USER_AGENT

@pytest.mark.parametrize("source", [yahoo.fetch_news, reddit.fetch_posts])
@pytest.mark.parametrize("error", [requests.HTTPError("403 Forbidden"), requests.HTTPError("429 Too Many Requests"), requests.Timeout("timeout")])
def test_source_errors_are_data(source, error, now):
    session = Mock()
    session.get.side_effect = error
    result = source("AAPL", now, 24, session)
    assert result.status == "error" and result.errors and not result.items


def test_invalid_xml_is_not_empty_success(now):
    session = Mock()
    session.get.return_value.content = b"<html/>"
    assert yahoo.fetch_news("AAPL", now, 24, session).status == "error"


def test_reddit_normalization(now):
    payload = {"data": {"children": [{"data": {"title": "$NVDA bullish", "selftext": "earnings beat", "created_utc": now.timestamp(), "permalink": "/r/stocks/comments/test"}}], "after": "next"}}
    result = reddit.parse_posts(payload, "NVDA")
    assert len(result.items) == 1 and "Search truncated" in result.warnings[-1]


def test_x_skips_without_auth(now, monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    result = x.fetch_posts("AAPL", now, 24, Mock())
    assert result.status == "skipped" and not result.items


def test_x_import_and_relevance(now):
    payload = {"data": [{"text": "$AAPL bullish", "created_at": now.isoformat(), "id": "123"},
                        {"text": "Chinese spy", "created_at": now.isoformat(), "id": "124"},
                        {"text": "$AAPL bullish", "id": "125"}]}
    result = x.fetch_posts("AAPL", now, 24, Mock(), payload=payload)
    assert len(result.items) == 1 and any("malformed" in w for w in result.warnings)
    assert x.parse_posts(payload, "SPY").items == []


def test_x_api_authorization_and_fields(now):
    session = Mock()
    session.get.return_value.json.return_value = {"data": [{"text": "$AAPL bullish", "created_at": now.isoformat(), "id": "1"}]}
    result = x.fetch_posts("AAPL", now, 24, session, token="test-not-a-secret")
    kwargs = session.get.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer test-not-a-secret"
    assert kwargs["params"]["tweet.fields"] == "created_at"
    assert result.status == "ok" and len(result.items) == 1


def test_x_partial_errors_exposed(now):
    payload = {"data": [{"text": "$AAPL bullish", "created_at": now.isoformat(), "id": "1"}], "errors": [{}]}
    assert x.fetch_posts("AAPL", now, 24, Mock(), payload=payload).status == "partial"


def test_x_api_error_does_not_leak_token(now):
    session = Mock()
    session.get.side_effect = requests.HTTPError("secret-token")
    result = x.fetch_posts("AAPL", now, 24, session, token="secret-token")
    assert result.status == "error" and "secret-token" not in str(result.errors)


def price_payload(now, latest_age=0):
    return {"chart": {"error": None, "result": [{"meta": {"regularMarketPrice": 101, "chartPreviousClose": 100, "regularMarketTime": now.timestamp()},
          "timestamp": [(now - timedelta(hours=6)).timestamp(), (now - timedelta(hours=latest_age)).timestamp()],
          "indicators": {"quote": [{"close": [100, 101]}]}}]}}


def test_price_aligned_window(now):
    out = yahoo.parse_price(price_payload(now), "AAPL", now, 6)
    assert out["usable_for_divergence"] and out["percent_change"] == 1


def test_weekend_or_stale_prices_suppressed(now):
    out = yahoo.parse_price(price_payload(now, latest_age=3), "AAPL", now, 6)
    assert not out["usable_for_divergence"] and out["percent_change"] is None


def test_price_failure_visible(now):
    session = Mock()
    session.get.side_effect = requests.HTTPError("429")
    price, status = yahoo.fetch_price("AAPL", now, 6, session)
    assert not price["usable_for_divergence"] and status.status == "error"
