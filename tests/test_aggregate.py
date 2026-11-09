from marketpulse.aggregate import analyze, summarize, direction


def status(*sources):
    return [{"ticker": "AAPL", "source": s, "status": "ok"} for s in sources]


def evaluate(items, now, price=None, statuses=None, history=None):
    return analyze("AAPL", items, now, 24, 6, price or {"usable_for_divergence": False},
                   statuses or status("yahoo"), history)


def sample(make_item, recent=3, earlier=3):
    return ([make_item(score=.6, id=f"new{i}") for i in range(recent)] +
            [make_item(hours=12, score=-.6, id=f"old{i}") for i in range(earlier)])


def test_summary(make_item):
    data = summarize([make_item(), make_item(score=-.6), make_item(score=0, source="x")])
    assert (data["volume"], data["bullish"], data["bearish"], data["neutral"]) == (3, 1, 1, 1)
    assert data["net_score"] == 0 and data["by_source"] == {"x": 1, "yahoo": 2}
    assert summarize([])["net_score"] is None


def test_first_run_shift_and_flat_price(make_item, now):
    out = evaluate(sample(make_item), now, {"usable_for_divergence": True, "percent_change": .1})
    assert out["comparison"]["direction_flip"]
    assert {a["kind"] for a in out["alerts"]} == {"sentiment_shift", "price_unconfirmed_shift"}


def test_confirmed_price_not_divergent(make_item, now):
    out = evaluate(sample(make_item), now, {"usable_for_divergence": True, "percent_change": 2})
    assert [a["kind"] for a in out["alerts"]] == ["sentiment_shift"]


def test_missing_stale_price_never_asserts_divergence(make_item, now):
    out = evaluate(sample(make_item), now, {"usable_for_divergence": False, "percent_change": .1})
    assert [a["kind"] for a in out["alerts"]] == ["sentiment_shift"]


def test_sparse_first_run_no_alerts(make_item, now):
    out = evaluate([make_item()], now)
    assert not out["alerts"] and not out["comparison"]["adequate"]


def test_volume_uses_hourly_rates(make_item, now):
    out = evaluate(sample(make_item, recent=6, earlier=3), now)
    assert out["comparison"]["volume_ratio"] == 6
    assert "unusual_observed_volume" in {a["kind"] for a in out["alerts"]}


def test_new_source_does_not_manufacture_shift(make_item, now):
    items = [make_item(source="x", id=str(i)) for i in range(10)] + [make_item(hours=12, score=-.8, id=f"old{i}") for i in range(10)]
    out = evaluate(items, now, statuses=status("yahoo", "x"))
    assert out["comparison"]["sources"] == [] and out["alerts"] == []


def test_partial_source_excluded_from_comparison(make_item, now):
    out = evaluate(sample(make_item), now, statuses=[{"ticker": "AAPL", "source": "yahoo", "status": "partial"}])
    assert not out["comparison"]["adequate"]


def test_snapshot_fallback(make_item, now):
    history = {"hours": 24, "tickers": {"AAPL": {"successful_sources": ["yahoo"],
               "recent": {"sources": {"yahoo": {"volume": 3, "score_sum": -1.8}}}}}}
    out = evaluate([make_item(id=str(i)) for i in range(3)], now, history=history)
    assert out["comparison"]["baseline"] == "non-overlapping historical snapshot"
    assert out["comparison"]["adequate"]


def test_snapshot_source_coverage_must_match(make_item, now):
    history = {"hours": 24, "tickers": {"AAPL": {"successful_sources": ["x", "yahoo"],
               "recent": {"sources": {"yahoo": {"volume": 3, "score_sum": -1.8}}}}}}
    assert not evaluate([make_item()]*3, now, history=history)["comparison"]["adequate"]


def test_opposite_price_confirms_no_direction(make_item, now):
    out = evaluate(sample(make_item), now, {"usable_for_divergence": True, "percent_change": -2})
    assert "price_unconfirmed_shift" in {a["kind"] for a in out["alerts"]}


def test_mean_delta_without_flip(make_item, now):
    items = [make_item(score=.8, id=f"new{i}") for i in range(3)] + [make_item(hours=12, score=.2, id=f"old{i}") for i in range(3)]
    out = evaluate(items, now)
    assert out["comparison"]["direction_flip"] is False
    assert out["comparison"]["delta"] == .6
    assert out["alerts"][0]["kind"] == "sentiment_shift"


def test_weaker_bullish_tone_is_bearish_shift(make_item, now):
    items = [make_item(score=.2, id=f"new{i}") for i in range(3)] + [make_item(hours=12, score=.8, id=f"old{i}") for i in range(3)]
    out = evaluate(items, now, {"usable_for_divergence": True, "percent_change": 2})
    assert out["alerts"][0]["direction"] == "bearish"
    assert "price_unconfirmed_shift" in {a["kind"] for a in out["alerts"]}


def test_small_delta_no_shift(make_item, now):
    items = [make_item(score=.5, id=f"new{i}") for i in range(3)] + [make_item(hours=12, score=.4, id=f"old{i}") for i in range(3)]
    assert evaluate(items, now)["alerts"] == []
