"""Common schema, source validation, relevance, time windows and deduplication."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

ALIASES = {"SPY": ("s&p 500", "spdr s&p", "spdr 500"), "AAPL": ("apple",),
           "MSFT": ("microsoft",), "NVDA": ("nvidia",), "JPM": ("jpmorgan", "jp morgan"),
           "XOM": ("exxon", "exxonmobil")}


@dataclass
class Item:
    ticker: str
    source: str
    timestamp: str
    url: str
    text: str
    label: str = "neutral"
    score: float = 0.0
    signals: list = field(default_factory=list)
    flags: list = field(default_factory=list)


@dataclass
class SourceResult:
    ticker: str
    source: str
    items: list[Item] = field(default_factory=list)
    status: str = "ok"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sampled: bool = True
    fetched: int = 0

    def fail(self, message):
        self.errors.append(message)
        self.status = "partial" if self.items else "error"

    def as_status(self):
        return {"ticker": self.ticker, "source": self.source, "status": self.status,
                "errors": self.errors, "warnings": self.warnings, "sampled": self.sampled,
                "fetched": self.fetched, "accepted": len(self.items)}


def clean(text):
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", str(text))).split())


def parse_time(value):
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value, timezone.utc)
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        value = parsedate_to_datetime(value)
    if value.tzinfo is None:
        raise ValueError("timestamp requires a timezone")
    return value.astimezone(timezone.utc)


def valid_url(url):
    try:
        parts = urlsplit(url)
        return parts.scheme in {"http", "https"} and bool(parts.hostname) and not any(c.isspace() for c in url)
    except (ValueError, TypeError):
        return False


def canonical_url(url):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")
             and k.lower() not in {"guccounter", "guce_referrer", "guce_referrer_sig"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"),
                       urlencode(sorted(query)), ""))


def relevant(text, ticker):
    # SPY is an ordinary word: bare case-insensitive 'spy' must not match espionage.
    cashtag = re.search(r"\$" + re.escape(ticker) + r"\b", text, re.I)
    symbol = re.search(r"(?<![\w$])" + re.escape(ticker) + r"(?!\w)", text)
    alias = any(re.search(r"\b" + re.escape(a) + r"\b", text, re.I) for a in ALIASES.get(ticker, ()))
    if ticker == "SPY" and symbol and not cashtag:
        symbol = re.search(r"\b(?:stock|etf|shares|options|trading|trade|bullish|bearish|market)\b", text, re.I)
    return bool(cashtag or symbol or alias)


def select(items, now, hours):
    if now.tzinfo is None or hours <= 0:
        raise ValueError("Use an aware clock and positive hours")
    seen_url, seen_text, output = set(), set(), []
    for item in sorted(items, key=lambda x: parse_time(x.timestamp), reverse=True):
        stamp = parse_time(item.timestamp)
        if not now - timedelta(hours=hours) <= stamp <= now:
            continue
        url_key = (item.ticker, item.source, canonical_url(item.url))
        # Different tracking URLs / repeated bot boilerplate do not inflate mentions.
        text_key = (item.ticker, item.source, re.sub(r"https?://\S+", "", clean(item.text)).casefold().strip())
        if url_key in seen_url or text_key in seen_text:
            continue
        seen_url.add(url_key)
        seen_text.add(text_key)
        output.append(item)
    return output
