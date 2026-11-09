from dataclasses import replace
from datetime import timedelta
import pytest
from marketpulse.normalize import parse_time, select, relevant, valid_url
from marketpulse.watchlist import Watchlist, load_watchlist, save_watchlist, symbols, validate

@pytest.mark.parametrize("text,ticker,match", [
    ("Chinese spy investigation", "SPY", False),
    ("SPY ETF has a new fee", "SPY", True),
    ("$spy bearish", "SPY", True),
    ("Apple launches a product", "AAPL", True),
    ("Pineapple season is here", "AAPL", False),
    ("JPMORGAN results", "JPM", True),
    ("I spy a good time", "SPY", False),
])
def test_relevance(text, ticker, match):
    assert relevant(text, ticker) is match


def test_dedupe_time_and_tracking(make_item, now):
    item = make_item()
    all_items = [item, replace(item, url=item.url + "?utm_source=x"),
                 make_item(hours=25, id="old"), make_item(hours=-1, id="future")]
    assert select(all_items, now, 24) == [item]


def test_same_item_different_tickers_preserved(make_item, now):
    assert len(select([make_item(), make_item(ticker="NVDA")], now, 24)) == 2

@pytest.mark.parametrize("value", ["not-a-date", "2026-11-08T12:00:00", None])
def test_invalid_dates(value):
    with pytest.raises((ValueError, TypeError)):
        parse_time(value)

@pytest.mark.parametrize("url", ["https://", "javascript:alert(1)", "https://a b", "http://[broken"])
def test_bad_url(url):
    assert not valid_url(url)


def test_watchlist_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("MARKETPULSE_WATCHLIST", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "watchlist.yml").write_text("tickers: [FAKE]")
    assert load_watchlist().symbols == ("SPY", "AAPL", "MSFT", "NVDA", "JPM", "XOM")


def test_watchlist_atomic_save(tmp_path):
    path = tmp_path / "nested" / "watchlist.yml"
    save_watchlist(Watchlist(("SPY",), ("aapl", "AAPL")), path)
    assert load_watchlist(path).symbols == ("SPY", "AAPL")
    assert not list(path.parent.glob(".marketpulse-*"))


def test_env_override_must_be_absolute(monkeypatch):
    monkeypatch.setenv("MARKETPULSE_WATCHLIST", "relative.yml")
    with pytest.raises(ValueError, match="absolute"):
        load_watchlist()

@pytest.mark.parametrize("data", [{}, {"tickers": []}, {"tickers": "AAPL"}, {"tickers": [123]}, {"wrong": []}, {"tickers": ["../bad"]}])
def test_watchlist_rejects_invalid(data):
    with pytest.raises(ValueError):
        validate(data)
