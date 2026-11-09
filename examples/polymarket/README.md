# Polymarket examples

`fixture.json` and `links.example.yml` are synthetic. The market id
`example-market-not-live` is not a Polymarket market. Do not treat the prices,
the 24h volume, or the liquidity figure as live evidence.

The history point is stamped `2026-11-09T08:00:00Z` and the Gamma `updatedAt`
is `2026-11-09T17:30:00Z`. Reproduce the window with `--as-of`:

```bash
.venv/bin/marketpulse --tickers JPM \
  --polymarket examples/polymarket/links.example.yml \
  --polymarket-fixture examples/polymarket/fixture.json \
  --as-of 2026-11-09T18:00:00Z \
  -o output --data-dir data
```

`--as-of` is rejected unless a fixture is set, so a live Yahoo/Reddit/X run
cannot be relabeled with a frozen clock. Yahoo, Reddit, and X are still
requested on that command; only Polymarket is served from the fixture.
A thin mention sample suppresses the divergence flag. That suppression is the
expected guardrail, not a missing price.

Discovery of real markets is a separate command and is not run by the fixture:

```bash
.venv/bin/marketpulse polymarket-search --query "Fed" --limit 5
```
