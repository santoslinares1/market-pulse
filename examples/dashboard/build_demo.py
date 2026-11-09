"""Write the packaged synthetic dashboard demo. No network and no live sources.

Run from the repository root:

    python examples/dashboard/build_demo.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from marketpulse.aggregate import analyze
from marketpulse.normalize import Item

ROOT = Path(__file__).resolve().parent / "demo"
NOW = datetime(2026, 11, 9, 18, tzinfo=timezone.utc)
RUN_ID = "synthetic-demo-20261109T180000Z"


def item(ticker, source, stamp, url, text, label, score):
    return Item(ticker, source, stamp, url, text, label, score, flags=["synthetic_demo"])


ITEMS = [
    item("AAPL", "yahoo", "2026-11-08T15:00:00+00:00", "https://example.com/demo/aapl-earlier-1",
         "Synthetic headline: Apple faces a lawsuit over a fictional contract.", "bearish", -0.6),
    item("AAPL", "yahoo", "2026-11-08T18:00:00+00:00", "https://example.com/demo/aapl-earlier-2",
         "Synthetic headline: Apple cuts guidance in this made-up demo.", "bearish", -0.55),
    item("AAPL", "reddit", "2026-11-09T02:30:00+00:00", "https://example.com/demo/aapl-earlier-3",
         "Synthetic post: Apple downgrade chatter in the demo corpus.", "bearish", -0.5),
    item("AAPL", "yahoo", "2026-11-09T13:10:00+00:00", "https://example.com/demo/aapl-recent-1",
         "Synthetic headline: Apple raises guidance in the demo's recent window.", "bullish", 0.72),
    item("AAPL", "x", "2026-11-09T14:00:00+00:00", "https://example.com/demo/aapl-recent-2",
         "Synthetic post: bullish comment about Apple. Not a real account.", "bullish", 0.64),
    item("AAPL", "yahoo", "2026-11-09T16:30:00+00:00", "https://example.com/demo/aapl-recent-3",
         "Synthetic headline: Apple earnings beat in the demo corpus.", "bullish", 0.7),
    item("JPM", "yahoo", "2026-11-07T15:00:00+00:00", "https://example.com/demo/jpm-1",
         "Synthetic headline: JPMorgan mention with no strong lexicon hit.", "neutral", 0.05),
    item("JPM", "reddit", "2026-11-08T16:00:00+00:00", "https://example.com/demo/jpm-2",
         "Synthetic post: mixed comment about JPMorgan in the demo.", "neutral", 0.0),
    item("JPM", "yahoo", "2026-11-09T15:00:00+00:00", "https://example.com/demo/jpm-3",
         "Synthetic headline: JPMorgan raises guidance in the demo only.", "bullish", 0.4),
    item("NVDA", "x", "2026-11-09T12:30:00+00:00", "https://example.com/demo/nvda-1",
         "Synthetic post: Nvidia headline in the demo corpus.", "neutral", 0.1),
    item("NVDA", "yahoo", "2026-11-08T02:30:00+00:00", "https://example.com/demo/nvda-boundary",
         "Synthetic headline used to show the Cordoba date boundary.", "neutral", 0.0),
]


def statuses():
    rows = []
    for ticker in ("AAPL", "JPM", "NVDA"):
        rows.append({
            "ticker": ticker, "source": "yahoo", "status": "ok", "errors": [],
            "warnings": ["Synthetic demo status. No Yahoo request was made."],
            "sampled": True, "fetched": 2, "accepted": 2,
        })
        rows.append({
            "ticker": ticker, "source": "reddit", "status": "error", "errors": ["HTTPError: 403"],
            "warnings": ["Synthetic error row so the dashboard can show a partial source. Not a live request."],
            "sampled": True, "fetched": 0, "accepted": 0,
        })
        rows.append({
            "ticker": ticker, "source": "x", "status": "skipped", "errors": [],
            "warnings": ["Synthetic skip. No X token is used by the demo."],
            "sampled": True, "fetched": 0, "accepted": 0,
        })
    return rows


def polymarket_payload():
    meaning = (
        "Synthetic share prices in [0, 1]. They are not Polymarket prints, "
        "not calibrated probabilities, and not a forecast."
    )
    return {
        "enabled": True,
        "offline_fixture": True,
        "fixture_example": True,
        "data_origin": "synthetic_demo",
        "price_meaning": meaning,
        "divergence_meaning": (
            "Divergence was not evaluated as a trading signal. This demo leaves "
            "the mapping unspecified on purpose."
        ),
        "warnings": ["Every Polymarket row in this folder is synthetic_demo, not an archived capture."],
        "links": [
            {
                "ticker": "JPM",
                "market_id": "synthetic-jpm-rate",
                "slug": "synthetic-example-rate-cut",
                "question": "Will the example committee cut the example rate in December?",
                "outcome": "Yes",
                "relation": "unspecified",
                "interpretation": (
                    "Synthetic mapping. Yes means the fictional committee cuts the example rate. "
                    "Unspecified tells the dashboard to show the price beside JPM mentions and not to invent a direction."
                ),
                "price": 0.5,
                "price_basis": "synthetic_demo_price",
                "updated_at": "2026-11-09T17:00:00+00:00",
                "closed": False,
                "liquidity": 5000,
                "liquidity_unit": "Synthetic demo units, not pUSD and not a live book.",
                "volume_24h": 1200,
                "volume_24h_unit": "Synthetic trailing-24h figure, not exchange volume.",
                "resolution_status": None,
                "resolution_source": "Synthetic resolution note, not an oracle.",
                "rules_text": (
                    "This fictional contract would resolve Yes if the example committee announced "
                    "an example rate cut. It is not a Polymarket rule."
                ),
                "source_url": "https://example.com/demo/polymarket/jpm",
                "status": "ok",
                "errors": [],
                "warnings": ["Synthetic demo series. Not a Polymarket capture."],
                "divergence_flag": None,
                "suppressed_reasons": ["relation_unspecified"],
                "limitations": [],
                "window": {
                    "current_price": 0.5,
                    "earlier_price": 0.42,
                    "delta": 0.08,
                    "current_observed_at": "2026-11-09T17:00:00+00:00",
                    "earlier_observed_at": "2026-11-07T15:00:00+00:00",
                    "current_provenance": "synthetic_demo",
                    "earlier_provenance": "synthetic_demo",
                    "note": "Both points are synthetic. The change is not a market move.",
                },
                "history": {
                    "provenance": "synthetic_demo",
                    "sampling": "four hand-written points for the demo",
                    "points_kept": 4,
                    "truncated": False,
                },
            },
            {
                "ticker": "NVDA",
                "market_id": "synthetic-nvda-thin",
                "slug": "synthetic-nvda-thin",
                "question": "Will the example chip event happen before the demo year ends?",
                "outcome": "Yes",
                "relation": "unspecified",
                "interpretation": "Synthetic one-point series so the dashboard can show insufficient history.",
                "price": 0.33,
                "price_basis": "synthetic_demo_price",
                "updated_at": "2026-11-09T16:00:00+00:00",
                "closed": False,
                "liquidity": None,
                "liquidity_unit": "Missing on purpose.",
                "volume_24h": None,
                "volume_24h_unit": "Missing on purpose.",
                "resolution_status": None,
                "resolution_source": None,
                "rules_text": "Synthetic thin series. No second observation exists, so no trend is defined.",
                "source_url": "https://example.com/demo/polymarket/nvda",
                "status": "partial",
                "errors": [],
                "warnings": ["Only one synthetic point. Do not draw a trend."],
                "divergence_flag": None,
                "suppressed_reasons": ["relation_unspecified", "no_aligned_price_baseline", "missing_or_zero_liquidity"],
                "limitations": ["liquidity_missing", "no_history"],
                "window": {
                    "current_price": 0.33,
                    "earlier_price": None,
                    "delta": None,
                    "note": "No earlier synthetic point. Baseline not fabricated.",
                },
                "history": {"provenance": "synthetic_demo", "points_kept": 1, "truncated": False},
            },
        ],
    }


def observations():
    jpm = [
        ("2026-11-07T15:00:00+00:00", 0.42),
        ("2026-11-08T15:00:00+00:00", 0.47),
        ("2026-11-09T12:00:00+00:00", 0.44),
        ("2026-11-09T17:00:00+00:00", 0.50),
    ]
    rows = []
    for stamp, price in jpm:
        rows.append({
            "observation_id": f"synthetic-jpm-{stamp}",
            "series_id": "synthetic-series-jpm",
            "ticker": "JPM",
            "market_id": "synthetic-jpm-rate",
            "slug": "synthetic-example-rate-cut",
            "question": "Will the example committee cut the example rate in December?",
            "outcome": "Yes",
            "price": price,
            "price_basis": "synthetic_demo_price",
            "observed_at": stamp,
            "observed_at_basis": "synthetic_demo",
            "ingested_at": NOW.isoformat(),
            "provenance": "synthetic_demo",
            "sampling": "hand-written demo point",
            "liquidity": 5000,
            "volume_24h": 1200,
            "closed": False,
            "resolution_status": None,
            "run_id": RUN_ID,
            "source_url": "https://example.com/demo/polymarket/jpm",
            "relation": "unspecified",
            "interpretation": "Synthetic mapping. Not a live Polymarket market.",
        })
    rows.append({
        "observation_id": "synthetic-jpm-2026-11-09T17:00:00+00:00",
        "series_id": "synthetic-series-jpm",
        "ticker": "JPM",
        "market_id": "synthetic-jpm-rate",
        "outcome": "Yes",
        "price": 0.50,
        "price_basis": "synthetic_demo_price",
        "observed_at": "2026-11-09T17:00:00+00:00",
        "observed_at_basis": "synthetic_demo",
        "provenance": "synthetic_demo",
        "run_id": RUN_ID,
    })
    rows.append({
        "observation_id": "synthetic-nvda-only",
        "series_id": "synthetic-series-nvda",
        "ticker": "NVDA",
        "market_id": "synthetic-nvda-thin",
        "slug": "synthetic-nvda-thin",
        "question": "Will the example chip event happen before the demo year ends?",
        "outcome": "Yes",
        "price": 0.33,
        "price_basis": "synthetic_demo_price",
        "observed_at": "2026-11-09T16:00:00+00:00",
        "observed_at_basis": "synthetic_demo",
        "provenance": "synthetic_demo",
        "sampling": "single hand-written point",
        "liquidity": None,
        "volume_24h": None,
        "closed": False,
        "run_id": RUN_ID,
        "source_url": "https://example.com/demo/polymarket/nvda",
    })
    return rows


def main():
    output = ROOT / "output"
    data = ROOT / "data"
    output.mkdir(parents=True, exist_ok=True)
    (data / "polymarket").mkdir(parents=True, exist_ok=True)
    (data / "snapshots").mkdir(parents=True, exist_ok=True)
    # One intentional duplicate of the first Apple headline. The dashboard must count it once.
    records = []
    for entry in ITEMS:
        records.append({
            "ticker": entry.ticker, "source": entry.source, "timestamp": entry.timestamp,
            "url": entry.url, "text": entry.text, "label": entry.label, "score": entry.score,
            "signals": [], "flags": list(entry.flags),
        })
    records.append(dict(records[0]))
    tickers = {}
    status_rows = statuses()
    price = {
        "percent_change": None, "usable_for_divergence": False,
        "note": "Synthetic demo has no Yahoo bars. Price context is absent, not zero.",
    }
    for ticker in ("AAPL", "JPM", "NVDA"):
        own = [entry for entry in ITEMS if entry.ticker == ticker]
        tickers[ticker] = analyze(ticker, own, NOW, 24, 6, {**price, "ticker": ticker}, status_rows, None)
    summary = {
        "schema_version": 1,
        "classifier_version": "lexicon-1",
        "generated_at": NOW.isoformat(),
        "run_id": RUN_ID,
        "hours": 24,
        "recent_hours": 6,
        "warnings": ["Synthetic demo run. Not a live MarketPulse capture."],
        "source_status": status_rows,
        "tickers": tickers,
        "data_origin": "synthetic_demo",
    }
    (output / "items.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output / "source_status.json").write_text(json.dumps(status_rows, indent=2) + "\n", encoding="utf-8")
    (output / "alerts.json").write_text(
        json.dumps([alert for block in tickers.values() for alert in block["alerts"]], indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "prices.json").write_text(
        json.dumps({ticker: {**price, "ticker": ticker} for ticker in tickers}, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "polymarket.json").write_text(json.dumps(polymarket_payload(), indent=2) + "\n", encoding="utf-8")
    (data / "polymarket" / "observations.json").write_text(
        json.dumps({"schema_version": 1, "observations": observations()}, indent=2) + "\n",
        encoding="utf-8",
    )
    # Two overlapping saved runs. The dashboard must list them and must not add their volumes.
    for name, when, volume in (
        ("synthetic-run-a.json", "2026-11-08T18:00:00+00:00", 4),
        ("synthetic-run-b.json", "2026-11-09T12:00:00+00:00", 4),
    ):
        snap = {
            "schema_version": 1,
            "classifier_version": "lexicon-1",
            "generated_at": when,
            "run_id": name,
            "hours": 24,
            "recent_hours": 6,
            "data_origin": "synthetic_demo",
            "tickers": {"AAPL": {"overall": {"volume": volume, "net_score": 0.1}}},
        }
        (data / "snapshots" / name).write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
    (data / "snapshots" / "broken.json").write_text("{", encoding="utf-8")
    manifest = {
        "kind": "synthetic_demo",
        "not_live": True,
        "title": "Synthetic MarketPulse dashboard demo",
        "actual_span": "2026-11-07T15:00:00Z through 2026-11-09T18:00:00Z",
        "display_timezone": "America/Cordoba (UTC-3)",
        "note": (
            "Hand-built corpus for offline interviews. Mentions, prices, liquidity, and source "
            "errors are synthetic. Polymarket rows are not archived provider observations."
        ),
    }
    (ROOT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote synthetic demo to {ROOT}")


if __name__ == "__main__":
    main()
