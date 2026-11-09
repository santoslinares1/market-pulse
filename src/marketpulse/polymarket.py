"""Opt-in, read-only Polymarket context for an explicit market-to-ticker mapping.

A selected outcome price is a market price proxy. It is not a calibrated
probability, and it does not imply a bullish or bearish equity view unless the
user's own mapping says how they want that price compared. Divergence flags are
heuristic investigation prompts. This module never places orders or reads wallets.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

from .normalize import parse_time
from .sources.polymarket import (
    PolymarketRateLimit,
    fetch_event_by_slug,
    fetch_market,
    fetch_price_history,
    search_public,
)
from .storage import atomic_text, save_json
from .watchlist import symbols

MAX_LINKS = 20
MAX_SEARCH_EVENTS = 5
MAX_MARKETS_PER_EVENT = 8
MIN_REPORTED_LIQUIDITY = 1000.0
PRICE_DEAD_ZONE = 0.03
STALE_AFTER = timedelta(hours=6)
MIN_RECENT_MENTIONS = 3
DIRECTION_THRESHOLD = 0.15
RULES_CAP = 4000
IDENT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
RELATIONS = {
    "unspecified",
    "higher_price_with_bullish",
    "higher_price_with_bearish",
}
LIQUIDITY_UNIT = (
    "Gamma liquidity field as reported. The field table does not name a currency; "
    "current Polymarket collateral is pUSD. MarketPulse does not convert it."
)
VOLUME_24H_UNIT = "Gamma volume24hr, trailing 24 hours, provider-reported (not converted)"
VOLUME_FIELD_NOTE = (
    "Gamma volume field. The docs table we relied on defines volume24hr as trailing "
    "24h volume and does not define this broader volume field's period, so it is stored "
    "only as provider context."
)


class PolymarketConfigError(ValueError):
    """The opt-in YAML or fixture is invalid. The radar should not run it."""


def _sha(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _json_list(value, *, field: str):
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} is not valid JSON") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"{field} JSON must be a list")
        return parsed
    raise ValueError(f"{field} must be a list or a JSON list string")


def _finite_unit_price(value, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{field} is not a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not a number") from exc
    if not math.isfinite(number) or number < 0 or number > 1:
        raise ValueError(f"{field} must be a finite price in [0, 1]")
    return round(number, 6)


def _optional_number(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _optional_time(value):
    if value is None or value == "":
        return None
    try:
        return parse_time(value)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _history_time(value):
    """Data API timestamps are epoch seconds. Milliseconds are accepted and labeled."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("history timestamp is not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("history timestamp is not numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError("history timestamp is not a positive finite epoch")
    basis = "epoch_seconds"
    if number > 10**12:
        number = number / 1000.0
        basis = "epoch_milliseconds"
    return parse_time(number), basis


def _blank_to_none(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_links(path: Path):
    """Load an explicit mapping. There is no default file and no invented market."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolymarketConfigError(f"Could not read Polymarket links: {exc}") from exc
    if not isinstance(data, dict) or set(data) - {"links"} or not isinstance(data.get("links"), list):
        raise PolymarketConfigError("Polymarket YAML must be an object with a links list and no other keys")
    if not data["links"]:
        raise PolymarketConfigError("Polymarket links list is empty. Omit --polymarket to leave the radar unchanged.")
    if len(data["links"]) > MAX_LINKS:
        raise PolymarketConfigError(f"At most {MAX_LINKS} Polymarket links are read in one run")
    links = []
    seen = set()
    for index, raw in enumerate(data["links"], start=1):
        if not isinstance(raw, dict):
            raise PolymarketConfigError(f"Link {index} must be a mapping")
        extra = set(raw) - {"ticker", "market_id", "slug", "outcome", "relation", "interpretation"}
        if extra:
            raise PolymarketConfigError(f"Link {index} has unsupported keys: {sorted(extra)}")
        try:
            ticker = symbols([raw.get("ticker")])[0]
        except (ValueError, TypeError) as exc:
            raise PolymarketConfigError(f"Link {index} ticker: {exc}") from exc
        market_id = _blank_to_none(raw.get("market_id"))
        slug = _blank_to_none(raw.get("slug"))
        if market_id is None and slug is None:
            raise PolymarketConfigError(f"Link {index} needs market_id or slug")
        if market_id is not None and not IDENT.fullmatch(str(market_id)):
            raise PolymarketConfigError(f"Link {index} market_id must match {IDENT.pattern}")
        if slug is not None and not IDENT.fullmatch(str(slug)):
            raise PolymarketConfigError(f"Link {index} slug must match {IDENT.pattern}")
        outcome = raw.get("outcome")
        if not isinstance(outcome, str) or not outcome.strip():
            raise PolymarketConfigError(f"Link {index} needs an explicit outcome label")
        outcome = outcome.strip()
        relation = raw.get("relation", "unspecified")
        if relation not in RELATIONS:
            raise PolymarketConfigError(
                f"Link {index} relation must be one of {sorted(RELATIONS)}. "
                "unspecified shows the price beside sentiment and never flags divergence."
            )
        interpretation = raw.get("interpretation")
        if not isinstance(interpretation, str) or len(" ".join(interpretation.split())) < 20:
            raise PolymarketConfigError(
                f"Link {index} needs an interpretation of at least 20 characters. "
                "State what the outcome means. Do not leave MarketPulse to infer equity direction."
            )
        key = (ticker, market_id or "", slug or "", outcome)
        if key in seen:
            raise PolymarketConfigError(f"Link {index} duplicates ticker/market/outcome")
        seen.add(key)
        links.append({
            "ticker": ticker,
            "market_id": market_id,
            "slug": slug,
            "outcome": outcome,
            "relation": relation,
            "interpretation": " ".join(interpretation.split()),
        })
    return links


def load_fixture(path: Path):
    """Offline Gamma-shaped payloads. Never treated as a live capture."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolymarketConfigError(f"Could not read Polymarket fixture: {exc}") from exc
    if not isinstance(data, dict):
        raise PolymarketConfigError("Polymarket fixture must be a JSON object")
    markets = data.get("markets", [])
    histories = data.get("histories", {})
    if not isinstance(markets, list) or not isinstance(histories, dict):
        raise PolymarketConfigError("Fixture needs a markets list and a histories object")
    if data.get("example") is not True and data.get("label") != "example-not-live":
        # Still usable, but callers should say when the file itself is labeled.
        pass
    return {
        "example": bool(data.get("example") or data.get("label") == "example-not-live"),
        "note": data.get("note") if isinstance(data.get("note"), str) else None,
        "markets": markets,
        "histories": histories,
    }


def _markets_in(payload):
    """Accept a market, an event, a keyset page, or a list of any of those."""
    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("question") is not None or node.get("outcomes") is not None or node.get("conditionId") is not None:
            found.append(node)
        nested = node.get("markets")
        if isinstance(nested, list):
            for item in nested:
                walk(item)

    walk(payload)
    # A market object also contains nothing named markets sometimes; walk already added it.
    # Nested copies can duplicate the parent if the parent itself is a market with nested markets.
    unique = []
    seen = set()
    for market in found:
        identity = (str(market.get("id")), str(market.get("slug")))
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(market)
    return unique


def _match_market(payload, link):
    markets = _markets_in(payload)
    if link.get("market_id"):
        matched = [m for m in markets if str(m.get("id")) == str(link["market_id"])]
        if matched:
            return matched[0]
        if len(markets) == 1 and not link.get("slug"):
            only = markets[0]
            if only.get("id") is None or str(only.get("id")) == str(link["market_id"]):
                return only
    if link.get("slug"):
        matched = [m for m in markets if str(m.get("slug")) == str(link["slug"])]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            raise ValueError("slug matched more than one market")
    if len(markets) == 1 and not link.get("market_id"):
        return markets[0]
    raise ValueError("payload did not contain the configured market")


def _asset_ids(market, count):
    version = market.get("version")
    warnings = []
    if version == "v2":
        raw = _json_list(market.get("positionIds"), field="positionIds")
        field = "positionIds"
    elif version == "v1":
        raw = _json_list(market.get("clobTokenIds"), field="clobTokenIds")
        field = "clobTokenIds"
    else:
        warnings.append(
            "Market version is not v1 or v2, so outcome token ids were not guessed from "
            "clobTokenIds/positionIds. Public price history was not requested."
        )
        return [None] * count, warnings
    if raw is None:
        warnings.append(f"{field} is absent; public price history was not requested for this outcome")
        return [None] * count, warnings
    if len(raw) != count:
        raise ValueError(f"{field} length {len(raw)} != outcome count {count}")
    return [None if item is None else str(item) for item in raw], warnings


def parse_outcomes(market):
    """Align labels, prices, and token ids. Reject misaligned or non-finite prices."""
    labels = _json_list(market.get("outcomes"), field="outcomes")
    prices = _json_list(market.get("outcomePrices"), field="outcomePrices")
    if not labels or not prices:
        raise ValueError("market is missing outcomes or outcomePrices")
    if len(labels) != len(prices):
        raise ValueError(f"outcomes length {len(labels)} != outcomePrices length {len(prices)}")
    if any(not isinstance(label, str) or not label.strip() for label in labels):
        raise ValueError("every outcome label must be a non-empty string")
    stripped = [label.strip() for label in labels]
    if len(set(stripped)) != len(stripped):
        raise ValueError("duplicate outcome labels; refusing to guess which price applies")
    asset_ids, warnings = _asset_ids(market, len(stripped))
    outcomes = []
    for label, price, asset_id in zip(stripped, prices, asset_ids):
        outcomes.append({
            "label": label,
            "price": _finite_unit_price(price, field=f"price for {label}"),
            "asset_id": asset_id,
        })
    return outcomes, warnings


def select_outcome(outcomes, label):
    exact = [item for item in outcomes if item["label"] == label]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        raise ValueError(f"outcome {label!r} matched more than once")
    folded = [item for item in outcomes if item["label"].casefold() == label.casefold()]
    if len(folded) == 1:
        return folded[0], f"outcome matched case-insensitively as {folded[0]['label']!r}"
    available = ", ".join(item["label"] for item in outcomes)
    raise ValueError(f"outcome {label!r} not in market outcomes: {available}")


def _event_meta(market):
    events = market.get("events") if isinstance(market.get("events"), list) else []
    event = events[0] if events and isinstance(events[0], dict) else {}
    return {
        "event_id": _blank_to_none(event.get("id")),
        "event_slug": _blank_to_none(event.get("slug")),
        "event_title": _blank_to_none(event.get("title")),
    }


def _site_url(market, event_slug):
    slug = _blank_to_none(market.get("slug"))
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    if slug:
        return f"https://polymarket.com/market/{slug}"
    return None


def parse_selected_market(payload, link):
    """Turn a Gamma market/event payload into one selected-outcome observation draft."""
    market = _match_market(payload, link)
    outcomes, warnings = parse_outcomes(market)
    chosen, note = select_outcome(outcomes, link["outcome"])
    if note:
        warnings.append(note)
    if link.get("market_id") and market.get("id") is not None and str(market.get("id")) != str(link["market_id"]):
        raise ValueError("fetched market id does not match the configured market_id")
    if link.get("slug") and market.get("slug") and str(market.get("slug")) != str(link["slug"]):
        warnings.append("configured slug differs from the fetched market slug; market_id was authoritative")
    event = _event_meta(market)
    rules = _blank_to_none(market.get("description"))
    rules_truncated = False
    if rules and len(rules) > RULES_CAP:
        rules = rules[:RULES_CAP]
        rules_truncated = True
        warnings.append(f"resolution rules text truncated to {RULES_CAP} characters")
    closed = market.get("closed")
    if closed is not None and not isinstance(closed, bool):
        closed = None
        warnings.append("closed was not a boolean; treated as unknown")
    archived = market.get("archived") if isinstance(market.get("archived"), bool) else None
    updated = _optional_time(market.get("updatedAt"))
    liquidity = _optional_number(market.get("liquidity"))
    if market.get("liquidity") not in (None, "") and liquidity is None:
        warnings.append("liquidity was present but not a finite number; stored as null")
    volume_24h = _optional_number(market.get("volume24hr"))
    if market.get("volume24hr") not in (None, "") and volume_24h is None:
        warnings.append("volume24hr was present but not a finite number; stored as null")
    volume_field = _optional_number(market.get("volume"))
    resolution_status = _blank_to_none(market.get("resolutionStatus"))
    uma = market.get("umaResolutionStatuses")
    uma_text = None
    if isinstance(uma, list):
        uma_text = "; ".join(str(item) for item in uma if str(item).strip()) or None
    return {
        "market_id": str(market.get("id") or link.get("market_id") or ""),
        "slug": _blank_to_none(market.get("slug")) or link.get("slug"),
        "question": _blank_to_none(market.get("question")),
        "condition_id": _blank_to_none(market.get("conditionId")),
        "version": _blank_to_none(market.get("version")),
        "outcome": chosen["label"],
        "configured_outcome": link["outcome"],
        "price": chosen["price"],
        "price_basis": "gamma_outcome_price",
        "price_note": (
            "Gamma outcomePrices entry aligned by outcome index. This is a share price in [0, 1], "
            "a market price proxy, not a guaranteed calibrated probability."
        ),
        "asset_id": chosen["asset_id"],
        "asset_id_field": "positionIds" if market.get("version") == "v2" else (
            "clobTokenIds" if market.get("version") == "v1" else None
        ),
        "outcomes": [{"label": item["label"], "price": item["price"]} for item in outcomes],
        "updated_at": updated.isoformat() if updated else None,
        "end_date": (_optional_time(market.get("endDate") or market.get("endDateIso")).isoformat()
                     if _optional_time(market.get("endDate") or market.get("endDateIso")) else None),
        "closed": closed,
        "archived": archived,
        "accepting_orders": market.get("acceptingOrders") if isinstance(market.get("acceptingOrders"), bool) else None,
        "resolution_status": resolution_status,
        "uma_resolution_statuses": uma_text,
        "resolution_source": _blank_to_none(market.get("resolutionSource")),
        "rules_text": rules,
        "rules_truncated": rules_truncated,
        "liquidity": liquidity,
        "liquidity_unit": LIQUIDITY_UNIT,
        "volume_24h": volume_24h,
        "volume_24h_unit": VOLUME_24H_UNIT,
        "volume_reported": volume_field,
        "volume_reported_note": VOLUME_FIELD_NOTE,
        "provider_one_day_price_change": _optional_number(market.get("oneDayPriceChange")),
        "provider_one_day_price_change_note": (
            "Gamma oneDayPriceChange is the provider's own 24h figure. It is not the configured "
            "MarketPulse window and is not used as a baseline."
        ),
        **event,
        "source_url": _site_url(market, event["event_slug"]),
        "source_url_note": "Constructed from the event or market slug. Gamma does not return a canonical website URL.",
        "warnings": warnings,
    }


def series_id(market_id: str, outcome: str) -> str:
    return _sha(str(market_id).strip(), outcome.strip())


def observation_id(series: str, observed_at: str, price_basis: str, price: float, provenance: str) -> str:
    return _sha(series, observed_at, price_basis, f"{price:.6f}", provenance)


def _observation(*, draft, link, observed_at, observed_at_basis, price, price_basis, provenance,
                 sampling, run_id, ingested_at, resolution_seconds=None):
    series = series_id(draft["market_id"], draft["outcome"])
    stamp = observed_at.astimezone(timezone.utc).isoformat()
    return {
        "observation_id": observation_id(series, stamp, price_basis, price, provenance),
        "series_id": series,
        "ticker": link["ticker"],
        "market_id": draft["market_id"],
        "slug": draft.get("slug"),
        "question": draft.get("question"),
        "outcome": draft["outcome"],
        "price": price,
        "price_basis": price_basis,
        "observed_at": stamp,
        "observed_at_basis": observed_at_basis,
        "ingested_at": ingested_at.astimezone(timezone.utc).isoformat(),
        "provenance": provenance,
        "sampling": sampling,
        "resolution_seconds": resolution_seconds,
        "liquidity": draft.get("liquidity"),
        "volume_24h": draft.get("volume_24h"),
        "closed": draft.get("closed"),
        "resolution_status": draft.get("resolution_status"),
        "run_id": run_id,
        "source_url": draft.get("source_url"),
        "relation": link["relation"],
        "interpretation": link["interpretation"],
    }


def load_observation_store(path: Path):
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolymarketConfigError(f"Polymarket history file is unreadable: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("observations"), list):
        raise PolymarketConfigError("Polymarket history file schema is not version 1")
    return data["observations"]


def merge_observations(existing, new_rows):
    """Idempotent by observation_id. Existing rows are kept; nothing is backfilled."""
    by_id = {}
    ordered = []
    for row in list(existing) + list(new_rows):
        if not isinstance(row, dict) or not row.get("observation_id"):
            continue
        identity = row["observation_id"]
        if identity in by_id:
            continue
        by_id[identity] = row
        ordered.append(row)
    return ordered


def write_observation_store(path: Path, rows):
    save_json(path, {"schema_version": 1, "observations": rows})


def _history_from_fixture(fixture, asset_id):
    if not fixture or not asset_id:
        return None
    raw = fixture.get("histories", {}).get(asset_id)
    if raw is None:
        raw = fixture.get("histories", {}).get(str(asset_id))
    if raw is None:
        return {"points": [], "pages": 0, "truncated": False, "bucket_seconds": None,
                "provenance": "offline_fixture", "sampling": "fixture had no history for this token"}
    points = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(points, list):
        raise ValueError("fixture history must be a list or an object with a data list")
    return {
        "points": points,
        "pages": 1,
        "truncated": bool(isinstance(raw, dict) and (raw.get("pagination") or {}).get("has_more")),
        "bucket_seconds": 3600,
        "provenance": "offline_fixture",
        "sampling": "offline fixture labeled as example data, not a live Data API capture",
    }


def _points_to_observations(history, draft, link, run_id, ingested_at):
    rows = []
    skipped = 0
    time_basis_note = None
    for point in history.get("points") or []:
        if not isinstance(point, dict):
            skipped += 1
            continue
        try:
            stamp, time_basis = _history_time(point.get("timestamp"))
            price = _finite_unit_price(point.get("price"), field="history price")
        except ValueError:
            skipped += 1
            continue
        resolution = point.get("resolution_seconds", point.get("resolutionSeconds"))
        if resolution is not None and (isinstance(resolution, bool) or not isinstance(resolution, (int, float))):
            skipped += 1
            continue
        sampling = history.get("sampling")
        if resolution == 0:
            sampling = (sampling or "") + "; resolution_seconds=0 exact tick"
        elif isinstance(resolution, (int, float)):
            sampling = (sampling or "") + f"; resolution_seconds={int(resolution)} bucket"
        provenance = history.get("provenance") or "data_api_v2_prices_history"
        if provenance == "offline_fixture":
            price_basis = "fixture_history_price"
        else:
            price_basis = "data_api_v2_price"
        rows.append(_observation(
            draft=draft, link=link, observed_at=stamp, observed_at_basis=time_basis,
            price=price, price_basis=price_basis, provenance=provenance, sampling=sampling,
            run_id=run_id, ingested_at=ingested_at,
            resolution_seconds=None if resolution is None else int(resolution),
        ))
        time_basis_note = time_basis
    return rows, skipped, time_basis_note


def _lookup_payload(link, fixture, session):
    if fixture is not None:
        for market in fixture.get("markets") or []:
            try:
                return _match_market(market if market.get("markets") or market.get("question") or market.get("outcomes") else {"markets": [market]}, link)
            except ValueError:
                continue
        # Fall back to searching the whole fixture document.
        try:
            return _match_market({"markets": fixture.get("markets") or []}, link)
        except ValueError as exc:
            raise ValueError(f"fixture has no payload for this link ({exc})") from exc
    try:
        if link.get("market_id"):
            return fetch_market(session, market_id=link["market_id"])
        return fetch_market(session, slug=link["slug"])
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 404 and link.get("slug") and not link.get("market_id"):
            return fetch_event_by_slug(session, link["slug"])
        raise


def _window_prices(rows, now, hours, recent_hours):
    """Pick current and earlier prices. Never synthesize the missing side."""
    cutoff = now - timedelta(hours=recent_hours)
    start = now - timedelta(hours=hours)
    parsed = []
    for row in rows:
        try:
            stamp = parse_time(row["observed_at"])
            price = _finite_unit_price(row["price"], field="stored price")
        except (ValueError, KeyError, TypeError):
            continue
        if stamp <= now:
            parsed.append((stamp, price, row))
    current_candidates = [item for item in parsed if item[0] > cutoff]
    earlier_candidates = [item for item in parsed if start <= item[0] <= cutoff]
    current = max(current_candidates, key=lambda item: item[0]) if current_candidates else None
    earlier = max(earlier_candidates, key=lambda item: item[0]) if earlier_candidates else None
    # If two observations share the earlier timestamp, prefer the provider history series.
    if earlier is not None:
        same_time = [item for item in earlier_candidates if item[0] == earlier[0]]
        provider = [item for item in same_time if item[2].get("provenance") == "data_api_v2_prices_history"]
        if provider:
            earlier = provider[-1]
    delta = None
    if current and earlier:
        delta = round(current[1] - earlier[1], 6)
    note = "Comparable window uses observation times, not a fabricated past price."
    if current is None:
        note = "No observation inside the recent window."
    elif earlier is None:
        note = "No observation at or before the recent-window cutoff inside the lookback. Baseline not fabricated."
    return {
        "current_price": None if current is None else current[1],
        "current_observed_at": None if current is None else current[2]["observed_at"],
        "current_provenance": None if current is None else current[2].get("provenance"),
        "current_price_basis": None if current is None else current[2].get("price_basis"),
        "earlier_price": None if earlier is None else earlier[1],
        "earlier_observed_at": None if earlier is None else earlier[2]["observed_at"],
        "earlier_provenance": None if earlier is None else earlier[2].get("provenance"),
        "earlier_sampling": None if earlier is None else earlier[2].get("sampling"),
        "delta": delta,
        "dead_zone": PRICE_DEAD_ZONE,
        "note": note,
    }


def _sentiment_tone(ticker_result):
    if not ticker_result:
        return None, 0
    recent = ticker_result.get("recent") or {}
    return recent.get("net_score"), int(recent.get("volume") or 0)


def _direction(score):
    if score is None:
        return "unavailable"
    if score >= DIRECTION_THRESHOLD:
        return "bullish"
    if score <= -DIRECTION_THRESHOLD:
        return "bearish"
    return "neutral"


def _mapped_tone(relation, delta):
    if delta is None or relation == "unspecified":
        return None
    up = delta >= PRICE_DEAD_ZONE
    down = delta <= -PRICE_DEAD_ZONE
    if not up and not down:
        return "flat"
    price_up = up
    if relation == "higher_price_with_bullish":
        return "bullish" if price_up else "bearish"
    if relation == "higher_price_with_bearish":
        return "bearish" if price_up else "bullish"
    return None


def assess_divergence(link, draft, window, ticker_result, *, observed_at_basis, now):
    """Return a flag or a suppression reason. Unsupported cases stay quiet."""
    reasons = []
    if link["relation"] == "unspecified":
        reasons.append("relation_unspecified")
    score, volume = _sentiment_tone(ticker_result)
    tone = _direction(score)
    if volume < MIN_RECENT_MENTIONS:
        reasons.append("insufficient_recent_mentions")
    elif tone not in {"bullish", "bearish"}:
        reasons.append("sentiment_not_directional")
    if draft.get("closed") is True or draft.get("archived") is True:
        reasons.append("closed_or_archived")
    elif draft.get("closed") is None:
        reasons.append("closed_state_unknown")
    liquidity = draft.get("liquidity")
    if liquidity is None or liquidity <= 0:
        reasons.append("missing_or_zero_liquidity")
    elif liquidity < MIN_REPORTED_LIQUIDITY:
        reasons.append("below_liquidity_threshold")
    updated = _optional_time(draft.get("updated_at"))
    if observed_at_basis == "fetch_time" or updated is None:
        reasons.append("provider_timestamp_missing")
    elif now - updated > STALE_AFTER:
        reasons.append("stale_provider_timestamp")
    if window.get("earlier_price") is None or window.get("current_price") is None or window.get("delta") is None:
        reasons.append("no_aligned_price_baseline")
    elif abs(window["delta"]) < PRICE_DEAD_ZONE:
        reasons.append("price_move_inside_dead_zone")
    mapped = _mapped_tone(link["relation"], window.get("delta"))
    flag = None
    if not reasons and mapped in {"bullish", "bearish"} and tone in {"bullish", "bearish"} and mapped != tone:
        flag = {
            "ticker": link["ticker"],
            "kind": "polymarket_context_divergence",
            "direction": "investigate",
            "message": (
                f"Heuristic context flag only: recent {link['ticker']} conversation tone is {tone} "
                f"while the selected outcome price moved {window['delta']:+.3f} under your mapping "
                f"{link['relation']}. This is not an objective probability, a causal link, an edge, "
                "or trading advice. Inspect the question, rules, and both series."
            ),
        }
    elif not reasons:
        reasons.append("no_opposite_move_under_mapping")
    limitations = []
    if draft.get("closed") is True:
        limitations.append("closed")
    if draft.get("archived") is True:
        limitations.append("archived")
    if liquidity is None:
        limitations.append("liquidity_missing")
    elif liquidity <= 0 or liquidity < MIN_REPORTED_LIQUIDITY:
        limitations.append("illiquid")
    if "stale_provider_timestamp" in reasons or "provider_timestamp_missing" in reasons:
        limitations.append("stale_or_freshness_unknown")
    if window.get("earlier_price") is None:
        limitations.append("no_history")
    return {
        "flag": flag,
        "suppressed_reasons": reasons,
        "sentiment_recent_net_score": score,
        "sentiment_recent_mentions": volume,
        "sentiment_tone": tone,
        "mapped_price_tone": mapped,
        "limitations": limitations,
        "thresholds": {
            "min_recent_mentions": MIN_RECENT_MENTIONS,
            "sentiment_direction": DIRECTION_THRESHOLD,
            "price_dead_zone": PRICE_DEAD_ZONE,
            "min_reported_liquidity": MIN_REPORTED_LIQUIDITY,
            "stale_after_hours": STALE_AFTER.total_seconds() / 3600,
        },
    }


def _status_for(link, state, errors, warnings):
    readable = state in {"ok", "partial"}
    return {
        "ticker": link["ticker"],
        "source": "polymarket",
        "status": state,
        "errors": errors,
        "warnings": warnings,
        "sampled": True,
        "fetched": 1 if readable else 0,
        "accepted": 1 if readable else 0,
        "market_id": link.get("market_id"),
        "outcome": link.get("outcome"),
    }


def run_polymarket(*, links, watchlist, fixture, session, now, hours, recent_hours,
                   history_path: Path, run_id: str, ticker_results, read_history=True):
    """Collect selected markets. One link failing does not raise."""
    try:
        existing = load_observation_store(history_path)
    except PolymarketConfigError as exc:
        warning = f"Polymarket history was not read or rewritten: {exc}"
        return {
            "enabled": True,
            "offline_fixture": fixture is not None,
            "fixture_example": bool(fixture and fixture.get("example")),
            "price_meaning": "Polymarket history file is unusable, so no price comparison was produced.",
            "divergence_meaning": "Divergence was not evaluated.",
            "links": [],
            "warnings": [warning],
            "new_observation_ids": [],
        }, [{
            "ticker": "*",
            "source": "polymarket",
            "status": "error",
            "errors": [warning],
            "warnings": [],
            "sampled": True,
            "fetched": 0,
            "accepted": 0,
        }], []
    new_rows = []
    reports = []
    statuses = []
    run_warnings = []
    if fixture is not None:
        run_warnings.append(
            "Polymarket input is an offline fixture, not a live capture. "
            + (fixture.get("note") or "Label the file before treating any number as evidence.")
        )
    watch = set(watchlist)
    for link in links:
        errors, warnings = [], []
        if link["ticker"] not in watch:
            warnings.append(f"{link['ticker']} is not in this run's watchlist; link skipped without a request")
            statuses.append(_status_for(link, "skipped", errors, warnings))
            reports.append({**link, "status": "skipped", "errors": errors, "warnings": warnings,
                            "divergence": None, "suppressed_reasons": ["ticker_not_in_watchlist"]})
            continue
        try:
            payload = _lookup_payload(link, fixture, session)
            draft = parse_selected_market(payload, link)
            warnings.extend(draft["warnings"])
            if fixture is None and link.get("market_id"):
                draft["api_url"] = f"https://gamma-api.polymarket.com/markets/{link['market_id']}"
            elif fixture is None and link.get("slug"):
                draft["api_url"] = f"https://gamma-api.polymarket.com/markets/slug/{link['slug']}"
            else:
                draft["api_url"] = None
            observed_basis = "provider_updated_at"
            observed_at = _optional_time(draft.get("updated_at"))
            if observed_at is None:
                observed_at = now
                observed_basis = "fetch_time"
                warnings.append("Gamma updatedAt missing or unparseable; observation time is the MarketPulse clock")
            provenance = "offline_fixture" if fixture is not None else "gamma_market"
            spot = _observation(
                draft=draft, link=link, observed_at=observed_at, observed_at_basis=observed_basis,
                price=draft["price"], price_basis=draft["price_basis"], provenance=provenance,
                sampling="single Gamma outcomePrices read", run_id=run_id, ingested_at=now,
            )
            history_rows = []
            history_meta = None
            if read_history and draft.get("asset_id"):
                if fixture is not None:
                    history_meta = _history_from_fixture(fixture, draft["asset_id"])
                else:
                    start = now - timedelta(hours=hours)
                    # end is exclusive. Add one second so a bucket stamped at `now` can be included.
                    history_meta = fetch_price_history(
                        session, draft["asset_id"],
                        start_ts=int(start.timestamp()), end_ts=int(now.timestamp()) + 1,
                    )
                history_rows, skipped, _basis = _points_to_observations(
                    history_meta, draft, link, run_id, now
                )
                if skipped:
                    warnings.append(f"Skipped {skipped} malformed price-history points")
                if history_meta.get("truncated"):
                    warnings.append("Price history truncated after 2 pages; the window may be incomplete")
                if history_meta.get("provenance") == "offline_fixture":
                    warnings.append("Price history provenance: offline fixture, not the live Data API")
                elif history_rows:
                    warnings.append(
                        "Price history provenance: data-api.polymarket.com/v2/prices-history "
                        "bucket_seconds=3600. Buckets are samples, not a continuous trade tape."
                    )
            elif read_history:
                warnings.append("No aligned token id; public price history was not requested")
            series_rows = [
                row for row in existing
                if row.get("series_id") == spot["series_id"]
            ] + history_rows + [spot]
            window = _window_prices(series_rows, now, hours, recent_hours)
            # The spot row is the configured read. If its timestamp falls on the
            # earlier side of the cutoff, do not pretend it is the recent price.
            divergence = assess_divergence(
                link, draft, window, ticker_results.get(link["ticker"]),
                observed_at_basis=observed_basis, now=now,
            )
            new_rows.extend(history_rows)
            new_rows.append(spot)
            state = "partial" if any("malformed" in item or "truncated" in item or "not requested" in item for item in warnings) else "ok"
            if divergence["suppressed_reasons"] and state == "ok":
                # Suppression is a limitation, not a failed read.
                state = "ok"
            statuses.append(_status_for(link, state, errors, warnings))
            reports.append({
                **{k: v for k, v in draft.items() if k != "warnings"},
                "ticker": link["ticker"],
                "relation": link["relation"],
                "interpretation": link["interpretation"],
                "status": state,
                "errors": errors,
                "warnings": warnings,
                "window": window,
                "divergence_flag": divergence["flag"],
                "suppressed_reasons": divergence["suppressed_reasons"],
                "sentiment_recent_net_score": divergence["sentiment_recent_net_score"],
                "sentiment_recent_mentions": divergence["sentiment_recent_mentions"],
                "sentiment_tone": divergence["sentiment_tone"],
                "mapped_price_tone": divergence["mapped_price_tone"],
                "limitations": divergence["limitations"],
                "thresholds": divergence["thresholds"],
                "history": None if history_meta is None else {
                    "provenance": history_meta.get("provenance"),
                    "sampling": history_meta.get("sampling"),
                    "points_kept": len(history_rows),
                    "pages": history_meta.get("pages"),
                    "truncated": history_meta.get("truncated"),
                    "bucket_seconds": history_meta.get("bucket_seconds"),
                },
            })
        except (PolymarketRateLimit, requests.RequestException, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            statuses.append(_status_for(link, "error", errors, warnings))
            reports.append({
                "ticker": link["ticker"],
                "market_id": link.get("market_id"),
                "slug": link.get("slug"),
                "outcome": link.get("outcome"),
                "relation": link["relation"],
                "interpretation": link["interpretation"],
                "question": None,
                "price": None,
                "status": "error",
                "errors": errors,
                "warnings": warnings,
                "window": None,
                "divergence_flag": None,
                "suppressed_reasons": ["market_unavailable"],
                "limitations": ["unavailable"],
            })
    merged = merge_observations(existing, new_rows)
    write_observation_store(history_path, merged)
    return {
        "enabled": True,
        "offline_fixture": fixture is not None,
        "fixture_example": bool(fixture and fixture.get("example")),
        "price_meaning": (
            "Selected prices are Polymarket outcome share prices in [0, 1]. "
            "They are market price proxies, not calibrated probabilities, causal effects, or trading signals."
        ),
        "divergence_meaning": (
            "A polymarket_context_divergence flag is a heuristic prompt to compare your explicit "
            "mapping with recent conversation tone. It is suppressed when the mapping, coverage, "
            "freshness, liquidity, or aligned history is insufficient. No fair value or edge is computed."
        ),
        "links": reports,
        "warnings": run_warnings,
        "new_observation_ids": [row["observation_id"] for row in new_rows],
    }, statuses, new_rows


def discover_markets(session, *, query=None, market_id=None, slug=None, limit=5):
    """Bounded discovery. Does not write a watchlist or invent a ticker link."""
    limit = max(1, min(int(limit), MAX_SEARCH_EVENTS))
    note = (
        "Discovery only. Copy a real market id and the exact outcome label into a links file. "
        "MarketPulse does not add these markets to a run by itself."
    )
    if market_id:
        if not IDENT.fullmatch(str(market_id)):
            raise PolymarketConfigError("market id contains unsupported characters")
        payload = fetch_market(session, market_id=str(market_id))
        return _discovery_document(note, events_or_markets=payload, limit=limit, query=None)
    if slug:
        if not IDENT.fullmatch(str(slug)):
            raise PolymarketConfigError("slug contains unsupported characters")
        try:
            payload = fetch_market(session, slug=str(slug))
        except requests.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status != 404:
                raise
            payload = fetch_event_by_slug(session, str(slug))
        return _discovery_document(note, events_or_markets=payload, limit=limit, query=None)
    if not query or not str(query).strip():
        raise PolymarketConfigError("polymarket-search needs --query, --market-id, or --slug")
    payload = search_public(session, str(query).strip())
    return _discovery_document(note, events_or_markets=payload, limit=limit, query=str(query).strip())


def _summarize_market(market):
    summary = {
        "market_id": None if market.get("id") is None else str(market.get("id")),
        "slug": _blank_to_none(market.get("slug")),
        "question": _blank_to_none(market.get("question")),
        "closed": market.get("closed") if isinstance(market.get("closed"), bool) else None,
        "outcomes": None,
        "parse_warning": None,
    }
    try:
        outcomes, warnings = parse_outcomes(market)
        summary["outcomes"] = [{"label": item["label"], "price": item["price"]} for item in outcomes]
        if warnings:
            summary["parse_warning"] = "; ".join(warnings)
    except ValueError as exc:
        summary["parse_warning"] = str(exc)
    return summary


def _discovery_document(note, *, events_or_markets, limit, query):
    provider_has_more = False
    events_out = []
    if isinstance(events_or_markets, dict) and isinstance(events_or_markets.get("events"), list):
        pagination = events_or_markets.get("pagination") or {}
        provider_has_more = bool(pagination.get("hasMore") or pagination.get("has_more"))
        source_events = events_or_markets["events"][:limit]
        for event in source_events:
            if not isinstance(event, dict):
                continue
            markets = event.get("markets") if isinstance(event.get("markets"), list) else []
            events_out.append({
                "event_id": None if event.get("id") is None else str(event.get("id")),
                "slug": _blank_to_none(event.get("slug")),
                "title": _blank_to_none(event.get("title")),
                "closed": event.get("closed") if isinstance(event.get("closed"), bool) else None,
                "markets": [_summarize_market(m) for m in markets[:MAX_MARKETS_PER_EVENT] if isinstance(m, dict)],
                "markets_truncated": len(markets) > MAX_MARKETS_PER_EVENT,
            })
        truncated = len(events_or_markets["events"]) > limit
    else:
        markets = _markets_in(events_or_markets)[:MAX_MARKETS_PER_EVENT]
        events_out.append({
            "event_id": None,
            "slug": None,
            "title": None,
            "closed": None,
            "markets": [_summarize_market(m) for m in markets],
            "markets_truncated": len(_markets_in(events_or_markets)) > MAX_MARKETS_PER_EVENT,
        })
        truncated = False
    warnings = []
    if provider_has_more:
        warnings.append(
            "Provider reported more search results. MarketPulse requested one public-search page "
            "and did not follow a cursor, because the search page does not document one."
        )
    if truncated:
        noun = "event" if limit == 1 else "events"
        warnings.append(f"Results truncated locally to {limit} {noun}")
    return {
        "query": query,
        "note": note,
        "provider_has_more": provider_has_more,
        "truncated": truncated or provider_has_more,
        "warnings": warnings,
        "events": events_out,
    }


def write_polymarket_files(directory: Path, payload):
    save_json(directory / "polymarket.json", payload)
    buffer = io.StringIO(newline="")
    fieldnames = [
        "ticker", "market_id", "slug", "question", "outcome", "price", "price_basis",
        "observed_at", "earlier_price", "price_delta", "volume_24h", "liquidity",
        "closed", "limitations", "divergence", "source_url", "warnings", "errors",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for link in payload.get("links") or []:
        window = link.get("window") or {}
        row = {
            "ticker": link.get("ticker"),
            "market_id": link.get("market_id"),
            "slug": link.get("slug"),
            "question": link.get("question"),
            "outcome": link.get("outcome"),
            "price": link.get("price"),
            "price_basis": link.get("price_basis"),
            "observed_at": (window or {}).get("current_observed_at") or link.get("updated_at"),
            "earlier_price": window.get("earlier_price"),
            "price_delta": window.get("delta"),
            "volume_24h": link.get("volume_24h"),
            "liquidity": link.get("liquidity"),
            "closed": link.get("closed"),
            "limitations": "; ".join(link.get("limitations") or []),
            "divergence": (link.get("divergence_flag") or {}).get("message"),
            "source_url": link.get("source_url"),
            "warnings": "; ".join(link.get("warnings") or []),
            "errors": "; ".join(link.get("errors") or []),
        }
        for key, value in list(row.items()):
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                row[key] = "'" + value
        writer.writerow(row)
    atomic_text(directory / "polymarket.csv", buffer.getvalue())
