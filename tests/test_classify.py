import pytest
from marketpulse.classify import classify

@pytest.mark.parametrize("text,ticker,label", [
    ("Apple beats estimates and raises guidance", "AAPL", "bullish"),
    ("Apple cuts guidance amid losses", "AAPL", "bearish"),
    ("Company earnings calendar this week", "AAPL", "neutral"),
    ("not bullish on Apple", "AAPL", "bearish"),
    ("not bearish on Apple", "AAPL", "bullish"),
    ("Don't buy AAPL", "AAPL", "bearish"),
    ("Don’t buy $NVDA", "NVDA", "bearish"),
    ("no fraud found at Apple", "AAPL", "bullish"),
    ("not not bullish", "AAPL", "bullish"),
    ("not worried but bullish", "AAPL", "bullish"),
    ("not sure. bullish", "AAPL", "bullish"),
    ("No news from Apple; bullish outlook", "AAPL", "bullish"),
    ("LONG reports quarterly update", "LONG", "neutral"),
    ("buyback update", "AAPL", "bullish"),
    ("buyer at the shortstop convention", "AAPL", "neutral"),
    ("$AAPL is an easy short from here", "AAPL", "bearish"),
    ("Tomorrow is going to be giga green $NVDA", "NVDA", "bullish"),
])
def test_labels(text, ticker, label):
    actual, score, signals, flags = classify(text, ticker)
    assert actual == label
    assert -1 <= score <= 1
    assert bool(signals) == (score != 0)


def test_phrase_does_not_double_count():
    _, _, signals, _ = classify("buy the dip", "AAPL")
    assert [s["signal"] for s in signals] == ["buy the dip"]


def test_negation_audit():
    label, score, signals, _ = classify("not bullish", "NVDA")
    assert signals == [{"signal": "bullish", "weight": -2, "negated": True}]


def test_url_and_cashtag_noise():
    assert classify("$BUY https://example.com/bearish @bullish", "BUY")[0] == "neutral"


def test_ticker_line_context():
    text = "$AAPL — Don't buy\n$MSFT — Buy zone $500–$520"
    assert classify(text, "AAPL")[0] == "bearish"
    assert classify(text, "MSFT")[0] == "bullish"
    assert "ticker_line_context" in classify(text, "MSFT")[3]


def test_promo_is_neutral_with_explanation():
    label, score, signals, flags = classify("$JPM may rise. Join WhatsApp. Follow him!", "JPM")
    assert label == "neutral" and score == 0 and not signals
    assert "promotional_noise_suppressed" in flags


def test_shared_ticker_line_keeps_directional_prose():
    label, score, signals, flags = classify("Tomorrow is going to be giga green\n\n$NVDA $MU $CRDO", "NVDA")
    assert label == "bullish"
    assert "multi_ticker_ambiguous" in flags
