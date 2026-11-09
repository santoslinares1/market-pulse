from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import pytest
from marketpulse import cli
from marketpulse.normalize import SourceResult
from marketpulse.report import render
from marketpulse.storage import load_history, save_json


def mocked_batch(make_item, ticker):
    item = make_item(ticker=ticker, text=f"${ticker} bullish")
    return ([SourceResult(ticker, "yahoo", items=[item], fetched=1),
             SourceResult(ticker, "reddit", status="error", errors=["HTTPError: 403"]),
             SourceResult(ticker, "x", status="skipped", warnings=["No token"]),
             SourceResult(ticker, "yahoo_price", warnings=["Market closed"])],
             {"ticker": ticker, "percent_change": None, "usable_for_divergence": False, "note": "Stale price"})


def test_cli_pipeline_offline(tmp_path, monkeypatch, now, make_item):
    monkeypatch.setattr(cli, "collect_ticker", lambda ticker, *args: mocked_batch(make_item, ticker))
    args = cli.parser().parse_args(["--tickers", "AAPL,MSFT", "-o", str(tmp_path / "out"), "--data-dir", str(tmp_path / "data")])
    result = cli.run(args, now=now)
    out = tmp_path / "out"
    assert {p.name for p in out.iterdir()} == {"sentiment_brief.md", "items.json", "items.csv", "alerts.json", "prices.json", "summary.json", "source_status.json"}
    text = (out / "sentiment_brief.md").read_text()
    assert "does not forecast prices" in text
    assert "403" in text and "No token" in text
    assert "America/Cordoba" in text and "2026-11-08 19:00" in text
    assert "Insufficient comparable baseline" in text
    assert len(json.loads((out / "items.json").read_text())) == 2
    assert len(list((tmp_path / "data" / "snapshots").glob("*.json"))) == 1


@pytest.mark.parametrize("argv", [["--hours", "0"], ["--hours", "nan"], ["--recent-hours", "inf"], ["--hours", "6", "--recent-hours", "6"], ["--hours", "169"]])
def test_cli_rejects_invalid_windows(argv):
    with pytest.raises(SystemExit) as error:
        cli.main(argv)
    assert error.value.code == 2


def test_snapshot_corruption_and_nonoverlap(tmp_path, now):
    old = {"schema_version": 1, "classifier_version": "lexicon-1", "generated_at": (now - timedelta(hours=7)).isoformat(),
           "recent_hours": 6, "tickers": {}}
    save_json(tmp_path / "old.json", old)
    save_json(tmp_path / "overlap.json", {**old, "generated_at": (now - timedelta(hours=2)).isoformat()})
    (tmp_path / "bad.json").write_text("not json")
    selected, warnings = load_history(tmp_path, now, 6)
    assert selected == old and len(warnings) == 1


def test_watchlist_env_absolute(tmp_path, monkeypatch):
    from marketpulse.watchlist import load_watchlist
    path = tmp_path / "watch.yml"
    path.write_text("market: [SPY]\ntickers: [MSFT]")
    monkeypatch.setenv("MARKETPULSE_WATCHLIST", str(path))
    assert load_watchlist().symbols == ("SPY", "MSFT")
