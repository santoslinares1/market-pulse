"""Validate the saved real-source capture offline; never create replacement evidence."""
import json
from pathlib import Path
from marketpulse.classify import classify
from marketpulse.aggregate import summarize
from marketpulse.normalize import Item
from marketpulse.report import render

root = Path(__file__).resolve().parents[1] / "examples" / "live"
run = json.loads((root / "summary.json").read_text(encoding="utf-8"))
items = [Item(**row) for row in json.loads((root / "items.json").read_text(encoding="utf-8"))]
for item in items:
    label, score, signals, flags = classify(item.text, item.ticker)
    assert (label, score, signals) == (item.label, item.score, item.signals), item.url
for ticker, data in run["tickers"].items():
    assert summarize([i for i in items if i.ticker == ticker]) == data["overall"], ticker
assert render(run, items) == (root / "sentiment_brief.md").read_text(encoding="utf-8")
print(f"Saved capture verified: {len(items)} ticker-item mentions; classifications, aggregates and memo match. No network used.")
