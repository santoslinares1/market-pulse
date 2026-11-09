"""Bounded public JSON search. 403/429 are source errors, never bypassed."""
import requests
from . import USER_AGENT, TIMEOUT
from ..normalize import Item, SourceResult, clean, parse_time, relevant, select, valid_url

SUBREDDITS = "wallstreetbets+stocks+investing+SecurityAnalysis"
SEARCH_URL = f"https://www.reddit.com/r/{SUBREDDITS}/search.json"


def parse_posts(payload, ticker):
    children = payload["data"]["children"]
    if not isinstance(children, list):
        raise ValueError("Invalid Reddit children")
    result = SourceResult(ticker, "reddit", fetched=len(children))
    for row in children:
        try:
            post = row["data"]
            text = clean(post.get("title", ""))
            body = clean(post.get("selftext", ""))
            if body and body not in {"[deleted]", "[removed]"}:
                text += "\n" + body
            if not text or not relevant(text, ticker):
                continue
            stamp = parse_time(post["created_utc"])
            url = "https://www.reddit.com" + post["permalink"]
            if not valid_url(url) or not post["permalink"].startswith("/r/"):
                raise ValueError("Invalid Reddit permalink")
            result.items.append(Item(ticker, "reddit", stamp.isoformat(), url, text[:6000]))
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            result.warnings.append(f"Skipped malformed Reddit post: {type(exc).__name__}")
    result.warnings.append("Search capped at 100 posts in four subreddits; English lexicon; comments not fetched")
    if payload["data"].get("after"):
        result.warnings.append("Search truncated: more results exist; volume is a lower bound")
    return result


def fetch_posts(ticker, now, hours, session):
    try:
        response = session.get(SEARCH_URL,
                               params={"q": ticker, "restrict_sr": "on", "sort": "new",
                                       "t": "week", "limit": 100, "raw_json": 1},
                               headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        response.raise_for_status()
        result = parse_posts(response.json(), ticker)
        result.items = select(result.items, now, hours)
        return result
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        result = SourceResult(ticker, "reddit")
        result.fail(f"{type(exc).__name__}: {exc}")
        return result
