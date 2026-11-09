"""Offline Polymarket adapter: parsing, windows, CLI, and BigQuery DDL."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
import requests

from marketpulse import cli
from marketpulse.bq import mentions_ddl, polymarket_ddl, polymarket_merge_sql, row_from_observation
from marketpulse.normalize import SourceResult
from marketpulse.polymarket import (
    PolymarketConfigError,
    load_links,
    parse_outcomes,
    run_polymarket,
    select_outcome,
    series_id,
)
from marketpulse.sources.polymarket import PolymarketRateLimit, fetch_price_history, public_get


INTERPRETATION = "Yes means the fictional committee cuts the example rate, and this sentence is only a user note."


def link(**overrides):
    base = {
        "ticker": "JPM",
        "market_id": "example-market-not-live",
        "slug": "example-rate-cut-not-live",
        "outcome": "Yes",
        "relation": "higher_price_with_bullish",
        "interpretation": INTERPRETATION,
    }
    base.update(overrides)
    return base


def market(**overrides):
    payload = {
        "id": "example-market-not-live",
        "slug": "example-rate-cut-not-live",
        "question": "Will the example committee cut the example rate?",
        "description": "Fictional rule text for the example market.",
        "resolutionSource": "Example note",
        "version": "v1",
        "outcomes": "[\"Yes\", \"No\", \"Other\"]",
        "outcomePrices": "[\"0.55\", \"0.40\", \"0.05\"]",
        "clobTokenIds": "[\"tok-yes\", \"tok-no\", \"tok-other\"]",
        "positionIds": ["pos-yes", "pos-no", "pos-other"],
        "closed": False,
        "archived": False,
        "liquidity": "5000",
        "volume24hr": 1200,
        "volume": "10",
        "updatedAt": "2026-11-08T21:30:00Z",
        "endDate": "2026-12-31T00:00:00Z",
        "events": [{"id": "evt", "slug": "example-event", "title": "Example event"}],
    }
    payload.update(overrides)
    return payload


def _epoch(moment):
    return int(moment.timestamp())


def fixture_for(payload, histories=None, now=None):
    if histories is None:
        if now is None:
            raise AssertionError("default history needs now")
        histories = {"tok-yes": {"data": [
            {"timestamp": _epoch(now - timedelta(hours=10)), "price": 0.40, "resolution_seconds": 3600},
        ]}}
    return {
        "example": True,
        "note": "unit fixture, not a live capture",
        "markets": [payload],
        "histories": histories,
    }


def run_one(tmp_path, now, payload, histories=None, **link_overrides):
    chosen = link(**link_overrides)
    payload, statuses, rows = run_polymarket(
        links=[chosen],
        watchlist=("JPM",),
        fixture=fixture_for(payload, histories, now=now),
        session=None,
        now=now,
        hours=24,
        recent_hours=6,
        history_path=tmp_path / "observations.json",
        run_id="run-test",
        ticker_results={"JPM": {"recent": {"net_score": -0.4, "volume": 3}}},
    )
    return payload, statuses, rows


def test_json_encoded_multi_outcome_alignment(now):
    outcomes, warnings = parse_outcomes(market())
    assert warnings == []
    assert [item["label"] for item in outcomes] == ["Yes", "No", "Other"]
    assert outcomes[0]["asset_id"] == "tok-yes"
    chosen, note = select_outcome(outcomes, "other")
    assert chosen["label"] == "Other" and chosen["price"] == 0.05 and note


def test_v2_uses_position_ids_not_clob_tokens():
    outcomes, _ = parse_outcomes(market(version="v2"))
    assert outcomes[1]["asset_id"] == "pos-no"


def test_unknown_version_does_not_guess_token_id():
    outcomes, warnings = parse_outcomes(market(version="v9"))
    assert outcomes[0]["asset_id"] is None
    assert any("not guessed" in warning for warning in warnings)


def test_malformed_misaligned_duplicate_and_nonfinite_prices():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_outcomes(market(outcomes="["))
    with pytest.raises(ValueError, match="length"):
        parse_outcomes(market(outcomePrices="[\"0.5\"]"))
    with pytest.raises(ValueError, match="duplicate"):
        parse_outcomes(market(outcomes="[\"Yes\", \"Yes\", \"No\"]"))
    with pytest.raises(ValueError, match="finite"):
        parse_outcomes(market(outcomePrices="[\"NaN\", \"0.4\", \"0.05\"]"))
    with pytest.raises(ValueError, match="\\[0, 1\\]"):
        parse_outcomes(market(outcomePrices="[\"1.2\", \"0.4\", \"0.05\"]"))
    with pytest.raises(ValueError, match="token|clobTokenIds"):
        parse_outcomes(market(clobTokenIds="[\"only-one\"]"))


def test_nested_event_payload_matches_inner_market(tmp_path, now):
    inner = market()
    event = {"id": "parent", "slug": "parent-slug", "title": "Parent", "markets": [inner]}
    payload, statuses, _rows = run_polymarket(
        links=[link()],
        watchlist=("JPM",),
        fixture={"example": True, "note": "nested", "markets": [event], "histories": fixture_for(inner, now=now)["histories"]},
        session=None, now=now, hours=24, recent_hours=6,
        history_path=tmp_path / "observations.json", run_id="nested",
        ticker_results={"JPM": {"recent": {"net_score": -0.4, "volume": 3}}},
    )
    assert statuses[0]["status"] == "ok"
    assert payload["links"][0]["question"].startswith("Will the example")
    assert payload["links"][0]["price"] == 0.55


def test_missing_and_zero_liquidity_suppress_divergence(tmp_path, now):
    missing, _, _ = run_one(tmp_path / "a", now, market(liquidity=None))
    zero, _, _ = run_one(tmp_path / "b", now, market(liquidity="0"))
    assert missing["links"][0]["liquidity"] is None
    assert "missing_or_zero_liquidity" in missing["links"][0]["suppressed_reasons"]
    assert zero["links"][0]["divergence_flag"] is None
    assert "illiquid" in zero["links"][0]["limitations"]


def test_closed_and_stale_markets_do_not_flag(tmp_path, now):
    closed, _, _ = run_one(tmp_path / "c", now, market(closed=True))
    stale, _, _ = run_one(tmp_path / "s", now, market(updatedAt="2026-11-01T00:00:00Z"))
    assert closed["links"][0]["divergence_flag"] is None
    assert "closed_or_archived" in closed["links"][0]["suppressed_reasons"]
    assert "stale_provider_timestamp" in stale["links"][0]["suppressed_reasons"]
    assert stale["links"][0]["divergence_flag"] is None


def test_no_history_does_not_fabricate_baseline(tmp_path, now):
    payload, _, rows = run_one(tmp_path, now, market(), histories={})
    window = payload["links"][0]["window"]
    assert window["earlier_price"] is None and window["delta"] is None
    assert "not fabricated" in window["note"]
    assert "no_aligned_price_baseline" in payload["links"][0]["suppressed_reasons"]
    assert rows and all(row["price_basis"] == "gamma_outcome_price" for row in rows)


def test_history_alignment_provenance_and_dead_zone(tmp_path, now):
    # Cutoff is now-6h. A point 30h ago is outside the 24h lookback.
    # A point 10h ago is the earlier price. The same instant in milliseconds
    # wins that tie because provider history is preferred over nothing else here;
    # both rows are fixture history, and the later one in the list is kept on a tie.
    histories = {"tok-yes": {"data": [
        {"timestamp": _epoch(now - timedelta(hours=30)), "price": 0.10, "resolution_seconds": 3600},
        {"timestamp": _epoch(now - timedelta(hours=10)), "price": 0.40, "resolution_seconds": 3600},
        {"timestamp": (_epoch(now - timedelta(hours=10)) + 1) * 1000, "price": 0.41, "resolution_seconds": 3600},
        {"timestamp": _epoch(now - timedelta(hours=1)), "price": 0.90, "resolution_seconds": 0},
    ]}}
    payload, _, rows = run_one(tmp_path, now, market(), histories=histories)
    window = payload["links"][0]["window"]
    assert window["earlier_price"] == 0.41
    assert window["earlier_provenance"] == "offline_fixture"
    # updatedAt is 30 minutes before `now`, later than the one-hour exact tick,
    # so the Gamma outcome price is the current observation. The 30h-old 0.10
    # price is outside the lookback and is not used as a baseline.
    assert window["current_price"] == 0.55
    bases = {row["observed_at_basis"] for row in rows}
    assert "epoch_milliseconds" in bases
    assert any("exact tick" in (row.get("sampling") or "") for row in rows)
    flat_histories = {"tok-yes": {"data": [
        {"timestamp": _epoch(now - timedelta(hours=10)), "price": 0.54, "resolution_seconds": 3600},
    ]}}
    flat, _, _ = run_one(tmp_path / "flat", now, market(), histories=flat_histories)
    assert "price_move_inside_dead_zone" in flat["links"][0]["suppressed_reasons"]


def test_divergence_requires_explicit_opposite_mapping(tmp_path, now):
    flagged, _, _ = run_one(tmp_path / "f", now, market())
    flag = flagged["links"][0]["divergence_flag"]
    assert flag["kind"] == "polymarket_context_divergence"
    assert "not an objective probability" in flag["message"]
    assert "trading advice" in flag["message"]
    quiet, _, _ = run_one(tmp_path / "u", now, market(), relation="unspecified")
    assert quiet["links"][0]["divergence_flag"] is None
    assert "relation_unspecified" in quiet["links"][0]["suppressed_reasons"]
    same, _, _ = run_one(
        tmp_path / "same", now, market(),
    )
    # Rebuild with bullish sentiment by calling run_polymarket directly.
    payload, _, _ = run_polymarket(
        links=[link()], watchlist=("JPM",), fixture=fixture_for(market(), now=now), session=None,
        now=now, hours=24, recent_hours=6, history_path=tmp_path / "same.json", run_id="same",
        ticker_results={"JPM": {"recent": {"net_score": 0.5, "volume": 3}}},
    )
    assert payload["links"][0]["divergence_flag"] is None
    assert "no_opposite_move_under_mapping" in payload["links"][0]["suppressed_reasons"]
    thin, _, _ = run_polymarket(
        links=[link()], watchlist=("JPM",), fixture=fixture_for(market(), now=now), session=None,
        now=now, hours=24, recent_hours=6, history_path=tmp_path / "thin.json", run_id="thin",
        ticker_results={"JPM": {"recent": {"net_score": -0.5, "volume": 1}}},
    )
    assert "insufficient_recent_mentions" in thin["links"][0]["suppressed_reasons"]


def test_history_store_is_idempotent(tmp_path, now):
    path = tmp_path / "observations.json"
    first, _, _ = run_polymarket(
        links=[link()], watchlist=("JPM",), fixture=fixture_for(market(), now=now), session=None,
        now=now, hours=24, recent_hours=6, history_path=path, run_id="one",
        ticker_results={"JPM": {"recent": {"net_score": -0.4, "volume": 3}}},
    )
    stored = json.loads(path.read_text())
    count = len(stored["observations"])
    assert count >= 2
    assert len({row["observation_id"] for row in stored["observations"]}) == count
    second, _, _ = run_polymarket(
        links=[link()], watchlist=("JPM",), fixture=fixture_for(market(), now=now), session=None,
        now=now, hours=24, recent_hours=6, history_path=path, run_id="two",
        ticker_results={"JPM": {"recent": {"net_score": -0.4, "volume": 3}}},
    )
    again = json.loads(path.read_text())
    assert len(again["observations"]) == count
    assert second["links"][0]["window"]["delta"] == first["links"][0]["window"]["delta"]
    assert {row["series_id"] for row in again["observations"]} == {series_id("example-market-not-live", "Yes")}


def test_bad_link_does_not_drop_a_valid_link(tmp_path, now):
    good = link()
    bad = link(market_id="other-market", slug="other-slug", outcome="Yes")
    payload, statuses, _ = run_polymarket(
        links=[bad, good], watchlist=("JPM",),
        fixture={"example": True, "note": "mixed", "markets": [market(outcomes="not-json")], "histories": {}},
        session=None, now=now, hours=24, recent_hours=6,
        history_path=tmp_path / "observations.json", run_id="mixed",
        ticker_results={"JPM": {"recent": {"net_score": 0, "volume": 0}}},
    )
    # The valid id is absent from this fixture, so both links fail visibly.
    assert [item["status"] for item in statuses] == ["error", "error"]
    assert payload["links"][0]["price"] is None and payload["links"][1]["price"] is None

    payload, statuses, _ = run_polymarket(
        links=[link(market_id="broken", slug="broken", outcome="Yes"), good],
        watchlist=("JPM",),
        fixture={"example": True, "note": "one good", "markets": [
            market(id="broken", slug="broken", outcomes="not-json"), market()],
                 "histories": fixture_for(market(), now=now)["histories"]},
        session=None, now=now, hours=24, recent_hours=6,
        history_path=tmp_path / "ok.json", run_id="mixed2",
        ticker_results={"JPM": {"recent": {"net_score": -0.4, "volume": 3}}},
    )
    assert statuses[0]["status"] == "error"
    assert statuses[1]["status"] == "ok"
    assert payload["links"][1]["price"] == 0.55


def test_link_for_other_ticker_is_not_fetched(tmp_path, now):
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("skipped link must not be fetched")

    payload, statuses, _rows = run_polymarket(
        links=[link(ticker="XOM")],
        watchlist=("JPM",),
        fixture=None,
        session=type("S", (), {"get": forbidden})(),
        now=now, hours=24, recent_hours=6,
        history_path=tmp_path / "observations.json", run_id="skip",
        ticker_results={},
    )
    assert calls == []
    assert statuses[0]["status"] == "skipped"
    assert payload["links"][0]["suppressed_reasons"] == ["ticker_not_in_watchlist"]


def test_config_rejects_missing_interpretation_and_unknown_relation(tmp_path):
    path = tmp_path / "links.yml"
    path.write_text("links:\n  - ticker: JPM\n    market_id: abc\n    outcome: 'Yes'\n    interpretation: short\n")
    with pytest.raises(PolymarketConfigError, match="interpretation"):
        load_links(path)
    path.write_text(
        "links:\n  - ticker: JPM\n    market_id: abcdef\n    outcome: 'Yes'\n"
        f"    relation: implies_rally\n    interpretation: {INTERPRETATION}\n"
    )
    with pytest.raises(PolymarketConfigError, match="relation"):
        load_links(path)
    path.write_text("markets: []\n")
    with pytest.raises(PolymarketConfigError, match="links"):
        load_links(path)


def test_corrupt_history_file_is_not_rewritten(tmp_path, now):
    path = tmp_path / "observations.json"
    path.write_text("{")
    payload, statuses, rows = run_polymarket(
        links=[link()], watchlist=("JPM",), fixture=fixture_for(market(), now=now), session=None,
        now=now, hours=24, recent_hours=6, history_path=path, run_id="x",
        ticker_results={},
    )
    assert statuses[0]["status"] == "error"
    assert rows == []
    assert path.read_text() == "{"
    assert payload["links"] == []


class FakeResponse:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.url = "https://gamma-api.polymarket.com/test"

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code} Client Error")
            error.response = self
            raise error


class FakeSession:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return self.responder(url, params)


def test_rate_limit_is_visible_and_not_retried():
    session = FakeSession(lambda url, params: FakeResponse(429, {"error": "slow down"}, {"Retry-After": "2"}))
    with pytest.raises(PolymarketRateLimit, match="429"):
        public_get(session, "https://gamma-api.polymarket.com/markets/1")
    assert len(session.calls) == 1


def test_history_pagination_stops_at_two_pages():
    pages = {"n": 0}

    def responder(url, params):
        pages["n"] += 1
        if pages["n"] == 1:
            return FakeResponse(200, {"data": [{"timestamp": 1794168000, "price": 0.2, "resolution_seconds": 3600}],
                                      "pagination": {"has_more": True, "next_cursor": "cursor-1"}})
        return FakeResponse(200, {"data": [{"timestamp": 1794171600, "price": 0.3, "resolution_seconds": 3600}],
                                  "pagination": {"has_more": True, "next_cursor": "cursor-2"}})

    session = FakeSession(responder)
    result = fetch_price_history(session, "tok", start_ts=1, end_ts=2)
    assert result["pages"] == 2 and result["truncated"] is True
    assert len(result["points"]) == 2
    assert session.calls[1][1]["cursor"] == "cursor-1"
    assert len(session.calls) == 2


def test_search_truncates_and_does_not_follow_has_more():
    from marketpulse.polymarket import discover_markets

    markets = [market(id=str(i), question=f"Question {i}?", outcomes='["Yes", "No"]',
                      outcomePrices='["0.5", "0.5"]', clobTokenIds='["a", "b"]') for i in range(9)]
    body = {"events": [{"id": "1", "slug": "fed", "title": "Fed event", "markets": markets},
                       {"id": "2", "slug": "other", "title": "Other"}],
            "pagination": {"hasMore": True, "totalResults": 40}}
    session = FakeSession(lambda url, params: FakeResponse(200, body))
    found = discover_markets(session, query="Fed", limit=1)
    assert len(session.calls) == 1
    assert len(found["events"]) == 1
    assert len(found["events"][0]["markets"]) == 8
    assert found["events"][0]["markets_truncated"] is True
    assert found["provider_has_more"] is True
    assert any("did not follow" in warning for warning in found["warnings"])


def _yahoo_batch(stamp):
    from marketpulse.normalize import Item

    def collect(ticker, *_args):
        item = Item(ticker, "yahoo", stamp, "https://example.com/jpm", f"${ticker} raises guidance", "bullish", 0.6)
        return ([
            SourceResult(ticker, "yahoo", items=[item], fetched=1),
            SourceResult(ticker, "reddit", status="error", errors=["HTTPError: 403"]),
            SourceResult(ticker, "x", status="skipped", warnings=["No token"]),
            SourceResult(ticker, "yahoo_price", warnings=["Market closed"]),
        ], {"ticker": ticker, "percent_change": None, "usable_for_divergence": False, "note": "Stale"})
    return collect


def test_default_radar_does_not_call_polymarket_or_change_outputs(tmp_path, monkeypatch):
    monkeypatch.delenv("MARKETPULSE_POLYMARKET", raising=False)
    monkeypatch.delenv("MARKETPULSE_POLYMARKET_FIXTURE", raising=False)
    monkeypatch.setattr(cli, "collect_ticker", _yahoo_batch((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()))

    def boom(*_args, **_kwargs):
        raise AssertionError("Polymarket network was called")

    monkeypatch.setattr("marketpulse.sources.polymarket.public_get", boom)
    out = tmp_path / "out"
    code = cli.main(["--tickers", "JPM", "-o", str(out), "--data-dir", str(tmp_path / "data")])
    assert code == 0
    text = (out / "sentiment_brief.md").read_text()
    assert "Polymarket" not in text
    assert "does not forecast prices" in text
    assert not (out / "polymarket.json").exists()
    names = {path.name for path in out.iterdir()}
    assert names == {"sentiment_brief.md", "items.json", "items.csv", "alerts.json", "prices.json", "summary.json", "source_status.json"}


def test_fixture_cli_is_offline_and_writes_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "collect_ticker", _yahoo_batch("2026-11-09T16:00:00+00:00"))
    monkeypatch.setattr(requests.Session, "get", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("live get")))
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "out"
    code = cli.main([
        "--tickers", "JPM",
        "--polymarket", str(root / "examples/polymarket/links.example.yml"),
        "--polymarket-fixture", str(root / "examples/polymarket/fixture.json"),
        "--as-of", "2026-11-09T18:00:00Z",
        "-o", str(out),
        "--data-dir", str(tmp_path / "data"),
    ])
    assert code == 0
    document = json.loads((out / "polymarket.json").read_text())
    assert document["offline_fixture"] is True and document["fixture_example"] is True
    link_row = document["links"][0]
    assert link_row["question"].startswith("Will the example committee")
    assert link_row["price"] == 0.55
    assert link_row["window"]["earlier_price"] == 0.4
    assert link_row["window"]["delta"] == 0.15
    assert link_row["volume_24h"] == 1200
    assert link_row["liquidity"] == 5000
    assert "not a live" in (out / "polymarket.csv").read_text() or "example committee" in (out / "polymarket.csv").read_text()
    brief = (out / "sentiment_brief.md").read_text()
    assert "Polymarket context" in brief
    assert "offline fixture" in brief
    assert "not calibrated" in brief.lower() or "not a guaranteed" in brief.lower() or "market price proxies" in brief
    stored = json.loads((tmp_path / "data" / "polymarket" / "observations.json").read_text())
    assert stored["schema_version"] == 1
    # One yahoo item is not enough coverage, so the demo must not claim divergence.
    assert link_row["divergence_flag"] is None
    assert "insufficient_recent_mentions" in link_row["suppressed_reasons"]
    # Yahoo evidence is still there.
    assert len(json.loads((out / "items.json").read_text())) == 1


def test_polymarket_http_failure_keeps_other_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "collect_ticker", _yahoo_batch((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()))
    monkeypatch.setattr(
        "marketpulse.sources.polymarket.public_get",
        lambda *_a, **_k: (_ for _ in ()).throw(PolymarketRateLimit("HTTP 429 from gamma. Not retried.")),
    )
    links = tmp_path / "links.yml"
    links.write_text(
        "links:\n"
        "  - ticker: JPM\n"
        "    market_id: '559651'\n"
        "    outcome: 'Yes'\n"
        "    relation: unspecified\n"
        f"    interpretation: {INTERPRETATION}\n"
    )
    out = tmp_path / "out"
    code = cli.main([
        "--tickers", "JPM", "--polymarket", str(links),
        "-o", str(out), "--data-dir", str(tmp_path / "data"),
    ])
    assert code == 0
    items = json.loads((out / "items.json").read_text())
    assert items and items[0]["source"] == "yahoo"
    status = json.loads((out / "source_status.json").read_text())
    poly = [row for row in status if row["source"] == "polymarket"]
    assert poly and poly[0]["status"] == "error" and "429" in poly[0]["errors"][0]
    assert "403" in (out / "sentiment_brief.md").read_text()


def test_as_of_rejected_for_live_runs():
    code = cli.main(["--tickers", "JPM", "--as-of", "2026-11-09T18:00:00Z"])
    assert code == 2


def test_bq_mentions_ddl_unchanged_and_polymarket_series_is_separate(capsys):
    mentions = mentions_ddl("demo-project", "marketpulse")
    assert "PARTITION BY DATE(published_at)" in mentions
    assert "polymarket_observations" not in mentions
    code = cli.main(["bq-ddl", "--bq-dataset", "demo-project.marketpulse"])
    assert code == 0
    assert "CREATE TABLE IF NOT EXISTS `demo-project.marketpulse.mentions`" in capsys.readouterr().out
    code = cli.main([
        "bq-ddl", "--bq-dataset", "demo-project.marketpulse", "--bq-series", "polymarket",
    ])
    assert code == 0
    rendered = capsys.readouterr().out
    assert "`demo-project.marketpulse.polymarket_observations`" in rendered
    assert "PARTITION BY DATE(observed_at)" in rendered
    assert "CLUSTER BY ticker, market_id" in rendered
    assert "CREATE TABLE IF NOT EXISTS `demo-project.marketpulse.mentions`" not in rendered
    sql = polymarket_merge_sql("demo-project", "marketpulse", "polymarket_observations", "_mp_poly_run")
    assert "ON T.observation_id = S.observation_id" in sql
    assert "mention_id" not in sql


def test_observation_row_is_utc_and_mergeable(monkeypatch, now):
    from marketpulse.bq import BigQueryObservationStore, export_polymarket_observations
    import marketpulse.bq as bq

    row = {
        "observation_id": "abc",
        "series_id": "series",
        "ticker": "jpm",
        "market_id": "example-market-not-live",
        "slug": "example",
        "outcome": "Yes",
        "question": "Example?",
        "price": 0.55,
        "price_basis": "gamma_outcome_price",
        "observed_at": now.isoformat(),
        "observed_at_basis": "provider_updated_at",
        "ingested_at": now.isoformat(),
        "provenance": "offline_fixture",
        "sampling": "fixture",
        "volume_24h": 10,
        "liquidity": 5000,
        "closed": False,
        "resolution_status": None,
        "run_id": "run-1",
        "source_url": "https://polymarket.com/event/example",
    }
    prepared = row_from_observation(row, run_id="run-1")
    assert prepared["observed_at"].endswith("UTC")
    assert prepared["ticker"] == "JPM"
    again = row_from_observation(row, run_id="run-2")
    assert again["observation_id"] == prepared["observation_id"]

    class FakeJob:
        def __init__(self, output_rows=None):
            self.output_rows = output_rows
            self.errors = None

        def result(self):
            return self

    class FakeBQ:
        class SchemaField:
            def __init__(self, name, field_type, mode="NULLABLE"):
                self.name, self.field_type, self.mode = name, field_type, mode

        class TimePartitioningType:
            DAY = "DAY"

        class TimePartitioning:
            def __init__(self, type_=None, field=None):
                self.type_, self.field = type_, field

        class Table:
            def __init__(self, table_id, schema=None):
                self.table_id = table_id
                self.full_table_id = table_id
                self.schema = schema

        class LoadJobConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class WriteDisposition:
            WRITE_APPEND = "WRITE_APPEND"

        class SourceFormat:
            NEWLINE_DELIMITED_JSON = "NEWLINE_DELIMITED_JSON"

    class FakeClient:
        def __init__(self):
            self.queries = []
            self.loaded = []
            self.deleted = []

        def create_table(self, table, exists_ok=True):
            return table

        def load_table_from_json(self, rows, table_id, job_config=None):
            self.loaded.append(rows)
            return FakeJob(output_rows=len(rows))

        def query(self, sql, location=None):
            self.queries.append(sql)
            return FakeJob()

        def delete_table(self, table_id, not_found_ok=True):
            self.deleted.append(table_id)

    monkeypatch.setattr(bq, "_import_bigquery", lambda: (FakeBQ, type("Gax", (), {})()))
    client = FakeClient()
    stats = export_polymarket_observations(
        [row, row], dataset_ref="demo-project.marketpulse", run_id="run-1", client=client,
    )
    assert stats["attempted"] == 2
    assert "observation_id" in client.queries[0]
    assert client.deleted
