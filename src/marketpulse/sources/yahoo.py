"""Yahoo RSS headlines and timestamped, window-aligned chart context."""
from datetime import timedelta, timezone
import math
import xml.etree.ElementTree as ET
import requests
from . import USER_AGENT, TIMEOUT
from ..normalize import Item, SourceResult, clean, parse_time, relevant, select, valid_url

FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"


def parse_rss(content, ticker):
    root = ET.fromstring(content)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Expected RSS channel, not an HTML/error document")
    result = SourceResult(ticker, "yahoo")
    skipped = 0
    for node in root.findall("channel/item"):
        result.fetched += 1
        try:
            text, url = clean(node.findtext("title", "")), node.findtext("link", "").strip()
            stamp = parse_time(node.findtext("pubDate", ""))
            if not text or not valid_url(url):
                raise ValueError("missing text or HTTP(S) URL")
            if not relevant(text, ticker):
                skipped += 1
                continue
            result.items.append(Item(ticker, "yahoo", stamp.isoformat(), url, text))
        except (ValueError, TypeError, OverflowError) as exc:
            result.warnings.append(f"Skipped malformed RSS item: {exc}")
    if skipped:
        result.warnings.append(f"Excluded {skipped} headlines without an explicit ticker/company reference")
    result.warnings.append("RSS is a bounded headline sample, not exhaustive news coverage")
    return result


def fetch_news(ticker, now, hours, session):
    try:
        response = session.get(FEED_URL, params={"s": ticker, "region": "US", "lang": "en-US"},
                               headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        response.raise_for_status()
        result = parse_rss(response.content, ticker)
        result.items = select(result.items, now, hours)
        return result
    except (requests.RequestException, ValueError, ET.ParseError, TypeError) as exc:
        result = SourceResult(ticker, "yahoo")
        result.fail(f"{type(exc).__name__}: {exc}")
        return result


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def parse_price(payload, ticker, now, recent_hours):
    chart = payload["chart"]
    if chart.get("error") or not chart.get("result"):
        raise ValueError("Yahoo chart returned an error or empty result")
    data = chart["result"][0]
    meta = data["meta"]
    price, previous = meta.get("regularMarketPrice"), meta.get("chartPreviousClose")
    quote_time = parse_time(meta["regularMarketTime"])
    output = {"ticker": ticker, "source": "yahoo_chart", "url": CHART_URL + ticker,
              "quote_time": quote_time.isoformat(), "reference_basis": "chartPreviousClose (chart-range reference; not necessarily last session close)", "regular_market_price": price if number(price) else None,
              "reference_pct": round((price / previous - 1) * 100, 4)
              if number(price) and number(previous) and previous > 0 else None,
              "percent_change": None, "window_start": None, "window_end": None,
              "usable_for_divergence": False, "note": "No fresh, window-aligned chart data"}
    stamps = data.get("timestamp") or []
    closes = data.get("indicators", {}).get("quote", [{}])[0].get("close") or []
    pairs = sorted((parse_time(t), c) for t, c in zip(stamps, closes)
                   if number(c) and c > 0 and parse_time(t) <= now)
    cutoff = now - timedelta(hours=recent_hours)
    baseline = [(t, c) for t, c in pairs if t <= cutoff]
    if pairs and baseline:
        start, end = baseline[-1], pairs[-1]
        fresh = (timedelta(0) <= now - end[0] <= timedelta(minutes=90)
                 and timedelta(0) <= cutoff - start[0] <= timedelta(minutes=90)
                 and end[0] > start[0])
        output.update(window_start=start[0].isoformat(), window_end=end[0].isoformat())
        if fresh:
            output.update(percent_change=round((end[1] / start[1] - 1) * 100, 4),
                          usable_for_divergence=True,
                          note=f"Observed chart return over approximately {recent_hours:g} hours; not a forecast")
    return output


def fetch_price(ticker, now, recent_hours, session):
    result = SourceResult(ticker, "yahoo_price", sampled=False)
    try:
        response = session.get(CHART_URL + ticker, params={"range": "5d", "interval": "30m"},
                               headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        response.raise_for_status()
        output = parse_price(response.json(), ticker, now, recent_hours)
        result.fetched = 1
        if not output["usable_for_divergence"]:
            result.warnings.append(output["note"] + "; market may be closed. No price-divergence alert.")
        return output, result
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
        result.fail(f"{type(exc).__name__}: {exc}")
        return {"ticker": ticker, "percent_change": None, "usable_for_divergence": False,
                "note": "Price source unavailable; divergence not evaluated"}, result
