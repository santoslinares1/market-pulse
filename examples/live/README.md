# Captured live demo — 2026-11-08 22:48 Argentina (America/Cordoba, UTC-3)

This is a **real-source, partially available** run, not a synthetic fixture. No posts,
headlines, publication times or numerical results were invented. It flags conversation
shifts; it does not forecast prices. Claims in source text are not verified facts.

## Exact command

```bash
cd /home/box/santos-job-search/market-pulse
.venv/bin/marketpulse --hours 24 --recent-hours 6 \
  --x-file examples/live/x_posts.json -o /tmp/marketpulse-demo/
```

Exit status: 0 (nonempty evidence, with source errors). The main deliverable is
`sentiment_brief.md`. All seven output artifacts were copied here, and `x_posts.json`
was also copied to the demo directory. JSON timestamps use explicit UTC offsets;
Markdown timestamps are Argentina (America/Cordoba, UTC-3).

## Actual results

| Ticker | Mentions | Yahoo / Reddit / X | Bullish / Bearish / Neutral | Net score | Overall tone |
|---|---:|---|---|---:|---|
| SPY | 4 | 1 / 0 / 3 | 0 / 0 / 4 | +0.0000 | neutral |
| AAPL | 7 | 3 / 0 / 4 | 2 / 2 / 3 | -0.0982 | neutral |
| MSFT | 6 | 2 / 0 / 4 | 3 / 0 / 3 | +0.1480 | neutral |
| NVDA | 12 | 8 / 0 / 4 | 3 / 5 / 4 | -0.0419 | neutral |
| JPM | 6 | 3 / 0 / 3 | 0 / 0 / 6 | +0.0000 | neutral |
| XOM | 4 | 0 / 0 / 4 | 0 / 0 / 4 | +0.0000 | neutral |

**39 ticker-item mentions: 17 Yahoo + 22 X + 0 Reddit.** There are 19 unique imported
X posts; multi-ticker posts can count under more than one ticker. Three JPM promotions
and one MSFT promotion were explicitly neutralized, not classified as positive market
sentiment. An overall neutral mean does not mean all items are neutral; inspect counts.

**No alerts fired.** Each ticker lacked at least three mentions in both windows from
comparable sources. In particular, recent-only imported X posts were not compared
against a Yahoo-only earlier window. There was no eligible prior non-overlapping snapshot.
All Yahoo price requests succeeded, but returned stale Friday quotes/bars (November 6,
around 18:00 Argentina / America/Cordoba, UTC-3). No fresh six-hour price interval was
available on Sunday night; no price-unconfirmed alert or prediction was asserted.

## Availability and errors

- Yahoo RSS succeeded for all six tickers. Explicit relevance filtering removed noisy
  feed headlines. XOM had no relevant in-window headline, not a Yahoo request failure.
- Reddit public JSON was attempted for all six tickers: HTTP 403 for SPY, MSFT, NVDA,
  JPM, XOM; a request read timeout for AAPL. There is **no successful Reddit sample**
  in this capture. Errors are preserved verbatim in `source_status.json` and the memo.
  The adapter did not bypass blocks or invent substitute posts.
- X connector retrieval succeeded. The runtime used the import, not an X bearer token.
- Yahoo price responses succeeded for all tickers but were stale for divergence analysis.

## X provenance and sampling warning

The Grok Bot `x` MCP namespace was discovered first; it had `search_posts_all`, not a
recent-post search tool. Six bounded searches were run, one per default ticker, plus
a more finance-specific SPY query after the initial results contained unrelated espionage
uses of the word spy. Each returned at most 10 posts. The connector stripped dollar signs
from initial queries. Exact query descriptions and the explicit source query window are
in `x_posts.json.provenance` (the machine window is UTC).

Nineteen actual returned posts were manually selected with their original IDs, exact text,
URLs and publication dates, favoring explicit ticker/finance references while avoiding
amplification of personal rumors. Some promotional examples were deliberately retained
to demonstrate noise suppression. This is a **purposive, recent-only subset**, not a random
or representative sample of X conversation, and must not be treated as population volume
or a historical sentiment trend. No claim in a post was verified independently.

## Two actual classification examples

- X, AAPL: “$AAPL is an easy short from here.” (exact excerpt) → **bearish, -0.5828**,
  matched `easy short`.
  https://x.com/GreedyLittlPig/status/2119602470158471335
- Yahoo, MSFT: “Microsoft (MSFT) Makes the 10 Best Dow Stocks for 2027 List as Azure Growth Speeds Up”
  → **bullish, +0.2449**, matched `growth`.
  https://finance.yahoo.com/markets/stocks/articles/microsoft-msft-makes-10-best-173628657.html?.tsrc=rss

These are heuristic language labels, not an endorsement of the speaker or a price target.
Lexicon limitations are visible in this capture: question headlines containing “Buy” can
score positive even when the author is asking rather than recommending. Conditional and
multi-ticker posts remain ambiguous. See the project README for full caveats.

## Reproducibility

`validation.txt` records offline tests, CLI help and dependency checks. All tests use
mocks/fixtures and do not depend on live access. The captured output is frozen evidence
of this run; fetching again changes timestamps and coverage. The imported posts will
age out of the default 24-hour window on future runs. `scripts/verify_capture.py`
reclassifies saved evidence and verifies its numbers without network access.
