"""Read MarketPulse artifacts and filter them. No network, no Streamlit, no Streamlit import."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..normalize import canonical_url, parse_time, valid_url
from ..storage import PROJECT_ROOT

ZONE = timezone(timedelta(hours=-3), "America/Cordoba")
ZONE_LABEL = "America/Cordoba (UTC-3)"
MENTION_SOURCES = ("yahoo", "reddit", "x")
SYNTHETIC_PROVENANCE = {"synthetic_demo", "offline_fixture", "example-not-live"}
KNOWN_OUTPUTS = (
    "items.json",
    "summary.json",
    "polymarket.json",
    "source_status.json",
    "alerts.json",
    "prices.json",
)


@dataclass
class Issue:
    path: str
    message: str


@dataclass
class Workspace:
    root: str
    kind: str
    title: str
    note: str
    items: list[dict] = field(default_factory=list)
    duplicate_mentions: int = 0
    summary: dict | None = None
    polymarket: dict | None = None
    observations: list[dict] = field(default_factory=list)
    duplicate_observations: int = 0
    source_status: list[dict] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    prices: dict | None = None
    runs: list[dict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def synthetic(self) -> bool:
        return self.kind == "synthetic_demo"


def demo_root() -> Path:
    return PROJECT_ROOT / "examples" / "dashboard" / "demo"


def local_date(value) -> date:
    return parse_time(value).astimezone(ZONE).date()


def format_local(value) -> str:
    try:
        return parse_time(value).astimezone(ZONE).strftime("%Y-%m-%d %H:%M ") + ZONE_LABEL
    except (TypeError, ValueError, OverflowError, OSError):
        return "unparsed timestamp"


def _read_json(path: Path, issues: list[Issue]):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(Issue(str(path), f"{type(exc).__name__}: could not read JSON"))
        return None


def _finite_score(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < -1 or number > 1:
        return None
    return number


def _finite_price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or number > 1:
        return None
    return number


def _clean_item(row, issues: list[Issue], origin: str):
    if not isinstance(row, dict):
        issues.append(Issue(origin, "Skipped a mention that was not an object"))
        return None
    ticker = str(row.get("ticker") or "").strip().upper()
    source = str(row.get("source") or "").strip().lower()
    url = row.get("url") if isinstance(row.get("url"), str) else ""
    text = row.get("text") if isinstance(row.get("text"), str) else ""
    try:
        stamp = parse_time(row.get("timestamp")).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        issues.append(Issue(origin, f"Skipped a mention with an unusable timestamp ({ticker or 'unknown'})"))
        return None
    score = _finite_score(row.get("score"))
    if not ticker or source not in MENTION_SOURCES or score is None:
        issues.append(Issue(origin, "Skipped a mention with a missing ticker, unknown source, or non-finite score"))
        return None
    label = row.get("label") if row.get("label") in {"bullish", "bearish", "neutral"} else "neutral"
    return {
        "ticker": ticker,
        "source": source,
        "timestamp": stamp,
        "url": url,
        "text": text,
        "label": label,
        "score": score,
        "signals": row.get("signals") if isinstance(row.get("signals"), list) else [],
        "flags": row.get("flags") if isinstance(row.get("flags"), list) else [],
    }


def _dedupe_items(items: list[dict]) -> tuple[list[dict], int]:
    seen = set()
    kept = []
    duplicates = 0
    for item in items:
        url = canonical_url(item["url"]) if valid_url(item["url"]) else item["url"]
        key = (item["ticker"], item["source"], url, item["timestamp"], item["text"])
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        kept.append(item)
    return kept, duplicates


def _clean_observation(row, issues: list[Issue], origin: str):
    if not isinstance(row, dict):
        issues.append(Issue(origin, "Skipped a Polymarket row that was not an object"))
        return None
    price = _finite_price(row.get("price"))
    try:
        stamp = parse_time(row.get("observed_at")).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        issues.append(Issue(origin, "Skipped a Polymarket row with an unusable observed_at"))
        return None
    if price is None or not str(row.get("ticker") or "").strip():
        issues.append(Issue(origin, "Skipped a Polymarket row with a missing ticker or a price outside [0, 1]"))
        return None
    identity = str(row.get("observation_id") or f"{row.get('market_id')}|{row.get('outcome')}|{stamp}|{price}")
    return {
        "observation_id": identity,
        "series_id": row.get("series_id"),
        "ticker": str(row.get("ticker")).strip().upper(),
        "market_id": None if row.get("market_id") is None else str(row.get("market_id")),
        "slug": row.get("slug"),
        "question": row.get("question") if isinstance(row.get("question"), str) else None,
        "outcome": row.get("outcome") if isinstance(row.get("outcome"), str) else None,
        "price": price,
        "price_basis": row.get("price_basis") if isinstance(row.get("price_basis"), str) else None,
        "observed_at": stamp,
        "provenance": row.get("provenance") if isinstance(row.get("provenance"), str) else None,
        "sampling": row.get("sampling") if isinstance(row.get("sampling"), str) else None,
        "liquidity": row.get("liquidity") if isinstance(row.get("liquidity"), (int, float)) and not isinstance(row.get("liquidity"), bool) else None,
        "volume_24h": row.get("volume_24h") if isinstance(row.get("volume_24h"), (int, float)) and not isinstance(row.get("volume_24h"), bool) else None,
        "closed": row.get("closed") if isinstance(row.get("closed"), bool) else None,
        "source_url": row.get("source_url") if isinstance(row.get("source_url"), str) else None,
        "interpretation": row.get("interpretation") if isinstance(row.get("interpretation"), str) else None,
    }


def _dedupe_observations(rows: list[dict]) -> tuple[list[dict], int]:
    seen = set()
    kept = []
    duplicates = 0
    for row in rows:
        if row["observation_id"] in seen:
            duplicates += 1
            continue
        seen.add(row["observation_id"])
        kept.append(row)
    return kept, duplicates


def _snapshot_index(directory: Path, issues: list[Issue]) -> list[dict]:
    if not directory.is_dir():
        return []
    runs = []
    for path in sorted(directory.glob("*.json")):
        payload = _read_json(path, issues)
        if payload is None:
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("tickers"), dict):
            issues.append(Issue(str(path), "Ignored a snapshot that is not a run object"))
            continue
        try:
            stamp = parse_time(payload.get("generated_at")).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            issues.append(Issue(str(path), "Ignored a snapshot with no generated_at"))
            continue
        volume = 0
        for block in payload["tickers"].values():
            overall = block.get("overall") if isinstance(block, dict) else None
            count = overall.get("volume") if isinstance(overall, dict) else None
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                volume += count
        runs.append({
            "path": path.name,
            "generated_at": stamp,
            "run_id": payload.get("run_id"),
            "reported_volume": volume,
            "data_origin": payload.get("data_origin"),
        })
    return runs


def load_workspace(root: Path, *, kind: str | None = None) -> Workspace:
    """Load one artifact directory. Snapshots are indexed and never added to mention rows."""
    root = Path(root)
    issues: list[Issue] = []
    manifest = _read_json(root / "MANIFEST.json", [])
    resolved_kind = kind or "local_artifacts"
    title = "Saved artifacts"
    note = "Loaded from disk. This dashboard does not refresh Yahoo, Reddit, X, Polymarket, or BigQuery."
    if isinstance(manifest, dict) and manifest.get("kind") == "synthetic_demo":
        resolved_kind = "synthetic_demo"
        title = str(manifest.get("title") or "Synthetic demo")
        note = str(manifest.get("note") or note)
    elif kind == "synthetic_demo":
        title = "Synthetic demo"
    workspace = Workspace(root=str(root), kind=resolved_kind, title=title, note=note, issues=issues)
    output = root / "output"
    items_payload = _read_json(output / "items.json", issues)
    if not (output / "items.json").is_file():
        issues.append(Issue(str(output / "items.json"), "No items.json in this folder"))
    elif items_payload is not None and not isinstance(items_payload, list):
        issues.append(Issue(str(output / "items.json"), "items.json must be a list of mentions"))
    elif isinstance(items_payload, list):
        cleaned = [row for row in (_clean_item(item, issues, "items.json") for item in items_payload) if row]
        workspace.items, workspace.duplicate_mentions = _dedupe_items(cleaned)
    summary = _read_json(output / "summary.json", issues)
    workspace.summary = summary if isinstance(summary, dict) else None
    if summary is not None and not isinstance(summary, dict):
        issues.append(Issue(str(output / "summary.json"), "summary.json must be an object"))
    polymarket = _read_json(output / "polymarket.json", issues)
    workspace.polymarket = polymarket if isinstance(polymarket, dict) else None
    if polymarket is not None and not isinstance(polymarket, dict):
        issues.append(Issue(str(output / "polymarket.json"), "polymarket.json must be an object"))
    observed = _read_json(root / "data" / "polymarket" / "observations.json", issues)
    rows = []
    if isinstance(observed, dict) and isinstance(observed.get("observations"), list):
        rows = observed["observations"]
    elif isinstance(observed, list):
        rows = observed
    elif observed is not None:
        issues.append(Issue(str(root / "data" / "polymarket" / "observations.json"), "observations file has no observations list"))
    cleaned_obs = [row for row in (_clean_observation(row, issues, "observations.json") for row in rows) if row]
    workspace.observations, workspace.duplicate_observations = _dedupe_observations(cleaned_obs)
    status = _read_json(output / "source_status.json", issues)
    if isinstance(status, list):
        workspace.source_status = [row for row in status if isinstance(row, dict)]
    elif status is not None:
        issues.append(Issue(str(output / "source_status.json"), "source_status.json must be a list"))
    alerts = _read_json(output / "alerts.json", issues)
    if isinstance(alerts, list):
        workspace.alerts = [row for row in alerts if isinstance(row, dict)]
    elif alerts is not None:
        issues.append(Issue(str(output / "alerts.json"), "alerts.json must be a list"))
    prices = _read_json(output / "prices.json", issues)
    workspace.prices = prices if isinstance(prices, dict) else None
    workspace.runs = _snapshot_index(root / "data" / "snapshots", issues)
    return workspace


def filter_items(items, tickers: set[str] | None, sources: set[str] | None, start: date | None, end: date | None):
    """None means the dimension is unconstrained. An empty set matches nothing."""
    kept = []
    for item in items:
        if tickers is not None and item["ticker"] not in tickers:
            continue
        if sources is not None and item["source"] not in sources:
            continue
        try:
            day = local_date(item["timestamp"])
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if start and day < start:
            continue
        if end and day > end:
            continue
        kept.append(item)
    return kept


def filter_observations(rows, tickers: set[str] | None, start: date | None, end: date | None):
    """Ticker and observed-at filters only. News-source filters do not apply."""
    kept = []
    for row in rows:
        if tickers is not None and row["ticker"] not in tickers:
            continue
        try:
            day = local_date(row["observed_at"])
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if start and day < start:
            continue
        if end and day > end:
            continue
        kept.append(row)
    return kept


def mean_score(items) -> float | None:
    if not items:
        return None
    return round(sum(item["score"] for item in items) / len(items), 4)


def label_mix(items) -> list[dict]:
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for item in items:
        counts[item["label"]] = counts.get(item["label"], 0) + 1
    return [{"label": label, "mentions": counts[label]} for label in ("bullish", "bearish", "neutral")]


def daily_sentiment(items) -> list[dict]:
    buckets: dict[str, list[float]] = {}
    for item in items:
        day = local_date(item["timestamp"]).isoformat()
        buckets.setdefault(day, []).append(item["score"])
    rows = []
    for day in sorted(buckets):
        scores = buckets[day]
        rows.append({
            "local_date": day,
            "mean_score": round(sum(scores) / len(scores), 4),
            "mentions": len(scores),
        })
    return rows


def group_observations(rows) -> list[dict]:
    groups: dict[tuple, list] = {}
    for row in rows:
        key = (row["ticker"], row.get("market_id"), row.get("outcome"))
        groups.setdefault(key, []).append(row)
    series = []
    for key, points in groups.items():
        ordered = sorted(points, key=lambda row: row["observed_at"])
        provenances = sorted({row.get("provenance") or "unspecified" for row in ordered})
        series.append({
            "ticker": key[0],
            "market_id": key[1],
            "outcome": key[2],
            "points": ordered,
            "provenances": provenances,
            "synthetic": any(mark in SYNTHETIC_PROVENANCE or "synthetic" in mark for mark in provenances),
            "enough_history": len(ordered) >= 2,
        })
    return series


def saved_windows(summary: dict | None, tickers: set[str] | None) -> list[dict]:
    if not isinstance(summary, dict) or not isinstance(summary.get("tickers"), dict):
        return []
    rows = []
    for ticker, block in summary["tickers"].items():
        if tickers is not None and ticker not in tickers:
            continue
        if not isinstance(block, dict):
            continue
        recent = block.get("recent") if isinstance(block.get("recent"), dict) else {}
        earlier = block.get("earlier") if isinstance(block.get("earlier"), dict) else {}
        overall = block.get("overall") if isinstance(block.get("overall"), dict) else {}
        rows.append({
            "ticker": ticker,
            "overall_volume": overall.get("volume"),
            "overall_net_score": overall.get("net_score"),
            "recent_volume": recent.get("volume"),
            "recent_net_score": recent.get("net_score"),
            "earlier_volume": earlier.get("volume"),
            "earlier_net_score": earlier.get("net_score"),
            "comparison": (block.get("comparison") or {}).get("note") if isinstance(block.get("comparison"), dict) else None,
        })
    return rows


def safe_href(url) -> str | None:
    if not isinstance(url, str) or not valid_url(url):
        return None
    if any(char in url for char in (" ", "\n", "\r", "\t")):
        return None
    return url


def is_synthetic_link(link: dict) -> bool:
    provenance = str(link.get("history", {}).get("provenance") if isinstance(link.get("history"), dict) else "")
    basis = str(link.get("price_basis") or "")
    return "synthetic" in provenance or "synthetic" in basis or link.get("data_origin") == "synthetic_demo"
