"""Read-only Polymarket public GETs. No auth, wallet, orders, or retries.

Documented surfaces used here (verified November 2026):

- Gamma market/event discovery: https://gamma-api.polymarket.com
  https://docs.polymarket.com/market-data/discover-markets
- Gamma market fields (outcomes, prices, liquidity, status):
  https://docs.polymarket.com/market-data/market-details
- Data API v2 price history:
  https://data-api.polymarket.com/v2/prices-history
  https://docs.polymarket.com/market-data/prices-order-books

A 429 is reported and not retried. Pagination is capped by the caller.
"""
from __future__ import annotations

import requests

from . import TIMEOUT, USER_AGENT

GAMMA_BASE = "https://gamma-api.polymarket.com"
HISTORY_URL = "https://data-api.polymarket.com/v2/prices-history"
MAX_HISTORY_PAGES = 2


class PolymarketRateLimit(RuntimeError):
    """Provider asked us to slow down. MarketPulse does not retry."""


def public_get(session, url, params=None):
    """One bounded unauthenticated GET. Raises on HTTP errors and non-JSON bodies."""
    response = session.get(
        url,
        params=params or None,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    if response.status_code == 429:
        retry = response.headers.get("Retry-After")
        raise PolymarketRateLimit(
            f"HTTP 429 from {url}. Retry-After={retry or 'absent'}. Not retried."
        )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = ""
        try:
            body = response.text[:180].replace("\n", " ")
            if body:
                detail = f" body={body!r}"
        except Exception:  # noqa: BLE001 — status is the useful fact
            detail = ""
        raise requests.HTTPError(f"{exc}{detail}", response=response) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f"Non-JSON response from {url}") from exc
    if not isinstance(payload, (dict, list)):
        raise ValueError(f"Unexpected JSON type from {url}")
    return payload


def fetch_market(session, *, market_id=None, slug=None):
    """GET one market by Gamma id or slug. Returns the JSON object."""
    if market_id:
        return public_get(session, f"{GAMMA_BASE}/markets/{market_id}")
    if slug:
        return public_get(session, f"{GAMMA_BASE}/markets/slug/{slug}")
    raise ValueError("market id or slug is required")


def fetch_event_by_slug(session, slug):
    return public_get(session, f"{GAMMA_BASE}/events/slug/{slug}")


def fetch_price_history(session, token_id, *, start_ts: int, end_ts: int):
    """Hourly buckets over an explicit range. At most two pages.

    Official constraints: start is inclusive, end is exclusive, and the span
    must be at most 15 days. bucket_seconds=3600 is inside the documented
    60–86400 range. A later page uses the documented `cursor` parameter.
    """
    points = []
    cursor = None
    pages = 0
    truncated = False
    has_more = False
    while pages < MAX_HISTORY_PAGES:
        params = {
            "token_id": str(token_id),
            "start": int(start_ts),
            "end": int(end_ts),
            "bucket_seconds": 3600,
        }
        if cursor:
            params["cursor"] = cursor
        payload = public_get(session, HISTORY_URL, params)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("prices-history response did not contain a data list")
        points.extend(payload["data"])
        pages += 1
        pagination = payload.get("pagination") or {}
        has_more = bool(pagination.get("has_more"))
        cursor = pagination.get("next_cursor") or None
        if not has_more or not cursor:
            break
    else:
        truncated = bool(has_more and cursor)
    if pages >= MAX_HISTORY_PAGES and has_more and cursor:
        truncated = True
    return {
        "points": points,
        "pages": pages,
        "truncated": truncated,
        "bucket_seconds": 3600,
        "provenance": "data_api_v2_prices_history",
        "sampling": "bucket_seconds=3600; a point with resolution_seconds=0 is an exact tick, not an hourly bucket",
    }


def search_public(session, query: str):
    """One public-search page. The provider may ignore limit; callers truncate."""
    return public_get(session, f"{GAMMA_BASE}/public-search", {"q": query})
