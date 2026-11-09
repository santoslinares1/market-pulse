"""X API v2 recent search or explicit imports. Never synthesize missing posts."""
from datetime import timedelta
from html import unescape
import json
import os
from pathlib import Path
import requests
from . import USER_AGENT, TIMEOUT
from ..normalize import Item, SourceResult, parse_time, relevant, select, valid_url

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


def parse_posts(payload, ticker):
    rows = payload if isinstance(payload, list) else payload.get("data", payload.get("posts", []))
    if not isinstance(rows, list):
        raise ValueError("X import must be a list, or an object with data/posts list")
    result = SourceResult(ticker, "x", fetched=len(rows))
    for post in rows:
        try:
            # Explicit ticker is optional metadata, not permission to accept irrelevant text.
            if post.get("ticker") and post["ticker"].upper() != ticker:
                continue
            text = unescape(post["text"]).strip()
            if not text or not relevant(text, ticker):
                continue
            stamp = parse_time(post.get("created_at", post.get("timestamp")))
            url = post.get("url") or f"https://x.com/i/web/status/{post['id']}"
            if not valid_url(url):
                raise ValueError("Invalid X URL")
            result.items.append(Item(ticker, "x", stamp.isoformat(), url, text,
                                     flags=["unverified_social_post"]))
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            result.warnings.append(f"Skipped malformed X post: {type(exc).__name__}")
    result.warnings.append("Bounded X sample, not population counts; posts are unverified claims, not financial facts")
    if isinstance(payload, dict) and payload.get("meta", {}).get("next_token"):
        result.warnings.append("Search truncated: more results exist")
    if isinstance(payload, dict) and payload.get("errors"):
        result.fail("X returned API partial errors")
    return result


def load_import(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, (dict, list)):
        raise ValueError("Invalid X import JSON")
    return payload


def fetch_posts(ticker, now, hours, session, payload=None, import_error=None, token=None):
    result = SourceResult(ticker, "x")
    if import_error:
        result.fail(f"X import: {import_error}")
        return result
    if payload is not None:
        try:
            result = parse_posts(payload, ticker)
            result.items = select(result.items, now, hours)
            result.warnings.append("Imported posts: coverage depends on export selection and age")
            return result
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            result.fail(f"X import: {type(exc).__name__}: {exc}")
            return result
    token = token if token is not None else os.environ.get("X_BEARER_TOKEN")
    if not token:
        result.status = "skipped"
        result.warnings.append("X skipped: set X_BEARER_TOKEN or use --x-file / MARKETPULSE_X_JSON. No posts fabricated.")
        return result
    try:
        start = now - timedelta(hours=min(hours, 167))
        query = f'"{ticker}" (stock OR ETF OR shares OR bullish OR bearish) lang:en -is:retweet'
        response = session.get(SEARCH_URL, params={"query": query, "max_results": 100,
                               "start_time": start.isoformat(), "tweet.fields": "created_at", "sort_order": "recency"},
                               headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        response.raise_for_status()
        result = parse_posts(response.json(), ticker)
        result.items = select(result.items, now, hours)
        if hours > 167:
            result.warnings.append("X recent search limited to less than seven days")
        return result
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
        # Do not serialize request headers or token-bearing exception representations.
        result.fail("X recent search failed (HTTP/network/invalid response); check API access and source availability")
        return result
