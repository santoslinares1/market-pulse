# MarketPulse

**An inspectable sentiment radar for an equity watchlist — not a market predictor.**

MarketPulse reads Yahoo Finance headlines and Reddit/X posts, labels their language
**bullish, bearish or neutral**, and flags unusual changes in observed conversation.
It keeps every retained item, its score, matched rules, timestamp and source link.
The portfolio goal is reproducible finance/data engineering: source adapters, explainable
features, explicit uncertainty, comparable time windows, local history and offline tests.

> This flags conversation shifts; it does not forecast prices. An alert is a prompt
> to inspect evidence, not a trading signal, investment recommendation or claim that
> sentiment consistently leads price. Social posts may be spam, misinformation or rumors.

## Quick start

Python 3.10+; no model download, browser, database, paid sentiment API or credentials required.
Runtime dependencies: `requests`, `PyYAML`. Development: `pytest`.

```bash
cd /home/box/santos-job-search/market-pulse
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/marketpulse --hours 24 --recent-hours 6 -o output
.venv/bin/python -m marketpulse --help
.venv/bin/pytest
.venv/bin/pip check
```

An editable install is convenient; regular wheel installations also include a default
watchlist. The project is standalone: HeadlineLens and StatementLens are not dependencies.

### Watchlist and paths

Default YAML, compatible with HeadlineLens:

```yaml
market: [SPY]
tickers: [AAPL, MSFT, NVDA, JPM, XOM]
```

The editable checkout resolves `watchlist.yml` from the **project root, not the current
working directory**. An installed wheel falls back to packaged defaults if the project
watchlist is absent. Set `MARKETPULSE_WATCHLIST=/absolute/path/watchlist.yml` or pass
`--watchlist PATH`. Override just one run with `--tickers AAPL,MSFT`. Order is preserved,
duplicates are removed, invalid/empty lists are rejected. `save_watchlist` validates and
atomically replaces YAML; comments may be rewritten. No command changes the default list.

Output defaults to project-root `output/`; snapshots default to project-root `data/snapshots/`.
For a read-only installation use `--data-dir /some/writable/directory`. Each snapshot
is an independent, fsync + temporary-file + replace JSON write; interrupted writes cannot
truncate the previous snapshot. Reports and watchlists use the same atomic-write helper.
Snapshots are local, not uploaded; keep or delete them as desired. There is no automatic
retention deletion. Do not commit tokens, `.env`, virtual environments or private exports.

## Sources and honest failure handling

- **Yahoo Finance RSS:** `https://feeds.finance.yahoo.com/rss/2.0/headline?s=TICKER&region=US&lang=en-US`.
  Titles only are scored to avoid duplicated summary text. Yahoo feeds can mix unrelated
  companies: an explicit ticker/company reference is required. This deliberately loses
  some relevant context. There is no historical completeness guarantee.
- **Reddit public JSON:** newest search of `wallstreetbets`, `stocks`, `investing`,
  `SecurityAnalysis`, combined in one query per ticker. At most 100 posts, last-week
  search followed by the exact local lookback filter. Post title and up to 6,000 characters
  of selftext; not comments. Contact User-Agent:
  `MarketPulse/0.1 (Santos Linares; santos9linares@gmail.com)`.
  Public JSON may return 403 or 429. Those become `source_status` errors; there is no
  block bypass, scraping fallback, retry storm or invented replacement data.
- **X:** API v2 `https://api.x.com/2/tweets/search/recent`, with `X_BEARER_TOKEN`
  supplied in the environment and appropriate X API access. One request per ticker,
  max 100 results. Never prints or stores the token. API errors are visible; recent
  search is capped below seven days. Alternatively import a JSON file with `--x-file`
  or `MARKETPULSE_X_JSON`; an import takes precedence and does not require a token.
  No token/file means a clean `skipped` status, **not fabricated posts**.
- **Price context:** Yahoo chart API, five-day / 30-minute bars. A recent-window return
  is computed only when the last bar and a baseline bar at/before the cutoff are each
  within 90 minutes of their target times. Weekend, closed-market or missing bars
  suppress price-divergence alerts. `chartPreviousClose` is also shown as a **chart-range
  reference**, not mislabeled as the prior session close. No adjusted-return guarantees.

Every adapter records status, fetched count, accepted count, errors, warnings and sampling
limits. A source failure is not neutral sentiment. One source failing does not abort other
sources. With no retained items the CLI still writes evidence/status files and exits 1;
configuration/write failures exit 2; a nonempty, possibly partial run exits 0.

### X import schema

```json
{"data": [{"id": "REAL_POST_ID", "text": "EXACT POST TEXT",
           "created_at": "2026-11-08T20:00:00Z",
           "url": "https://x.com/USER/status/REAL_POST_ID"}]}
```

This schema example is a placeholder, not demo evidence. A plain array or `posts` array
also works; `timestamp` can replace `created_at`. A dated post needs text and a valid
HTTP(S) link (or an ID from which an X status URL is built). Optional `ticker` metadata
restricts attribution; text must still pass relevance checks. Naive/missing timestamps
are rejected rather than replaced with the fetch time. Future/out-of-window rows and
same-source duplicate text/links are removed per ticker. Imports are offline snapshots:
rerunning an old live import will naturally produce fewer/no items after 24 hours.

```bash
.venv/bin/marketpulse --hours 24 --recent-hours 6 \
  --x-file examples/live/x_posts.json -o output
```

`examples/live/README.md` documents the actual captured run and X import provenance.
Live output is evidence of a source fetch, not a validated financial news service.

## Transparent scoring

`classify.py` holds a deliberately small English finance lexicon (for example,
`raises guidance`, `earnings beat`, `bullish`, `downgrade`, `cuts guidance`, `lawsuit`).
No ticker is itself a sentiment signal; substring matches and URLs/handles are ignored.
Multiword rules are matched longest first without overlapping single-word double counts.
Negation flips a rule's sign within the preceding three tokens, stopping at punctuation
or contrast words. Some contractions are normalized. Multi-cashtag lists are reduced to
the ticker's own line when possible; broad multi-ticker prose stays flagged as ambiguous.
Obvious WhatsApp/follow-this-blogger/retired-Goldman promotion templates are retained as
mentions but neutralized with an explicit `promotional_noise_suppressed` flag.

Per-item score = `tanh(sum(rule_weights) / 3)`, rounded to four decimals, bounded [-1, 1].
Label = bullish at >=0.2, bearish at <=-0.2, otherwise neutral. `signals` exposes each
weight and negation. A neutral label can mean no known signals, balanced signals or
suppressed promotion — **not** proof that the underlying content is unbiased.
Overall net score is the mean of item scores; absent evidence is null, never zero.
Ticker aggregates include source counts, label counts, score sum, mean and volume.

## Shift and unusual-activity rules

Default lookback: 24h; recent: last 6h; baseline: earlier 6–24h. Windows are based on
publication timestamps, not fetch timestamps. Only sources present in both comparison
windows with successful status are compared; a newly added X export cannot by itself
manufacture a shift against a Yahoo-only baseline.

1. Require **>=3 comparable mentions per window**. Score direction is bullish at >=0.15,
   bearish at <=-0.15, otherwise neutral. A directional recent mean triggers a
   `sentiment_shift` when its delta is >=0.35 in absolute value or the direction flips
   between bullish and bearish. Alert direction follows the signed score delta; a less
   bullish recent mean can therefore be a bearish shift. These thresholds are explicit heuristics, not calibrated
   probabilities or market-beating findings.
2. A `price_unconfirmed_shift` additionally requires usable window-aligned prices and
   either an absolute price change <=0.5% or a price move opposite to sentiment. Missing
   or stale prices cannot establish divergence or claim that a price reaction is coming.
3. `unusual_observed_volume` requires >=6 recent / >=3 baseline comparable mentions and
   **>=3x the hourly mention rate**, not a raw 6h-versus-18h count comparison. Counts are
   of retained sampled mentions, not total Reddit/X traffic. Source caps, publishing
   schedules and export selection can create false spikes; inspect coverage warnings.
4. If the within-run baseline is too thin, try the latest eligible snapshot from the
   previous seven days. It must have the same classifier version, lookback/recent-window
   settings and successful source set, and its recent window must **not overlap** the
   current one. This is historical context, not a same-day seasonal baseline. A thin
   first run remains explicitly insufficient; there is no fake zero baseline.

The program writes local alerts; it does not send email, X posts, trading orders or push
notifications. Scheduling/notification infrastructure is intentionally out of scope.

## Output artifacts

| File | Purpose |
|---|---|
| `sentiment_brief.md` | Main analyst-style memo: ticker overview, alerts, comparisons, quoted evidence, source errors, guardrails |
| `items.json` | Retained text, labels/scores, signals, flags, publication dates and links |
| `items.csv` | Spreadsheet-friendly evidence snippets (up to 1,000 chars); formula-like strings escaped |
| `summary.json` | Aggregates, settings, windows, comparisons and price context |
| `alerts.json` | Machine-readable local alerts; empty array is valid |
| `source_status.json` | Per-ticker/source availability, counts, errors and warnings |
| `prices.json` | Observed price data, freshness and whether divergence can be evaluated |

Markdown displays dates in **Argentina (America/Cordoba, UTC-3)**; machine JSON uses
ISO-8601 timestamps with explicit offsets (normally UTC). No timestamp is fabricated.

## Layout and validation

- `sources/`: independent Yahoo, Reddit, X adapters with bounded requests/timeouts.
- `normalize.py`: evidence schema, relevance checks, time validation and deduplication.
- `classify.py`: auditable lexicon, negation/context/noise handling.
- `aggregate.py`: source-level statistics, comparable windows and alert rules.
- `storage.py`, `watchlist.py`: atomic persistence and cwd-safe configuration.
- `cli.py`, `report.py`: orchestration, structured files and Markdown memo.
- `polymarket.py`: opt-in, read-only Polymarket context. Absent unless you pass a links file.
- `tests/`: offline fixtures/mocks for classification, negative cases, HTTP errors,
  aggregation, thresholds, coverage mismatches, stale prices, snapshots, rendering and CLI.

Tests require no network. Live sources may change or be blocked; reproducible offline tests
are distinct from the captured live demo. See `examples/live/validation.txt` for execution results.


## Optional BigQuery mention history

Default installs and default CLI runs **never** call Google Cloud. BigQuery is an
opt-in warehouse for retained mention evidence so you can run SQL over history.

### Install (local)

```bash
.venv/bin/pip install -e '.[dev,bigquery]'
```

Core `.[dev]` remains enough for the radar, offline tests, and local artifacts.

### One-time GCP setup (you do this later — not part of a default run)

1. Create or select a GCP project and enable the **BigQuery API** (billing is a GCP console decision; MarketPulse does not provision or spend for you).
2. Create a dataset in your chosen location (or let `bq-init` create dataset+table).
3. Authenticate Application Default Credentials (ADC). **No API keys or JSON key files belong in this repo.**

```bash
gcloud auth application-default login
# optional: gcloud config set project YOUR_PROJECT
```

Least privilege for a working identity (adjust to your org policy):

- `bigquery.datasets.create` (only if using `bq-init` to create the dataset)
- `bigquery.tables.create` / `bigquery.tables.update` / `bigquery.tables.delete` (staging table)
- `bigquery.tables.get`, `bigquery.tables.updateData`
- `bigquery.jobs.create`

### Schema plan / DDL (no cloud required)

```bash
.venv/bin/marketpulse bq-ddl --bq-dataset YOUR_PROJECT.YOUR_DATASET
# checked-in copy: sql/mentions_ddl.sql
```

Table `mentions` is partitioned by `DATE(published_at)` and clustered by `ticker, source`.
Stable `mention_id` = SHA-256 of `ticker|source|canonical_url` (or external id when present).
`published_at` is the item timestamp (UTC); `ingested_at` is load time (UTC).

### Explicit init (creates dataset/table; never hidden in a normal radar run)

```bash
.venv/bin/marketpulse bq-init --bq-dataset YOUR_PROJECT.YOUR_DATASET --bq-location US
```

### Opt-in export after a local run

Local Markdown/JSON/CSV/snapshots are always written first. BigQuery runs only when
`--bq-dataset` (or `MARKETPULSE_BQ_DATASET`) is set:

```bash
.venv/bin/marketpulse --hours 24 --recent-hours 6 -o output \
  --bq-dataset YOUR_PROJECT.YOUR_DATASET --bq-location US
```

Or export a prior `items.json` without refetching:

```bash
.venv/bin/marketpulse bq-export --items output/items.json \
  --bq-dataset YOUR_PROJECT.YOUR_DATASET --bq-location US
```

**Idempotency:** loads go to a per-run staging table, then `MERGE` on `mention_id`.
This is not streaming `insertId` best-effort dedupe. If the same mention is seen again
or `classifier_version` changes, MERGE updates score/label/signals/flags/classifier/
`ingested_at`/`run_id` while keeping `mention_id` and `published_at` stable (current-state
upsert, not a full score audit log).

If opt-in export fails (missing ADC, IAM, network, missing table), MarketPulse prints a
clear error and exits **3** after preserving local reports.

Env overrides: `MARKETPULSE_BQ_DATASET`, `MARKETPULSE_BQ_LOCATION`, `MARKETPULSE_BQ_TABLE`.

### Checked-in SQL examples (manual in console / `bq query`)

Replace `YOUR_PROJECT.YOUR_DATASET` before running. Set a maximum bytes billed in the UI/CLI.
These queries analyze **retained sampled mentions** only — not global conversation volume
and not predictive performance.

| File | Purpose |
|---|---|
| `sql/mentions_ddl.sql` | CREATE TABLE DDL |
| `sql/counts_by_ticker_source.sql` | Counts / label mix by ticker & source |
| `sql/label_trend_hourly_daily.sql` | Hourly and daily bullish/bearish/neutral trends |
| `sql/recent_vs_earlier_windows.sql` | Recent vs earlier window aggregates |
| `sql/dedup_and_history.sql` | mention_id uniqueness and rescore provenance |

There is no default `marketpulse` subcommand that executes arbitrary SQL against your
project (avoids injection and surprise bytes billed). Run the `.sql` files yourself.

Polymarket observations, when you opt in, use a **separate** table. The mentions
schema above is unchanged.

```bash
.venv/bin/marketpulse bq-ddl --bq-dataset YOUR_PROJECT.YOUR_DATASET --bq-series polymarket
# checked-in copy: sql/polymarket_observations_ddl.sql
# window query: sql/polymarket_window_changes.sql
.venv/bin/marketpulse bq-init --bq-dataset YOUR_PROJECT.YOUR_DATASET --bq-series polymarket
.venv/bin/marketpulse bq-export-polymarket \
  --observations data/polymarket/observations.json \
  --bq-dataset YOUR_PROJECT.YOUR_DATASET
```

`bq-ddl` and `bq-init` still default to `--bq-series mentions`. A normal radar run
exports Polymarket rows only when both `--bq-dataset` and a Polymarket links file
were used. That export is still opt-in and is not executed by the test suite.

## Optional Polymarket context

Default installs and default CLI runs **never** call Polymarket. There is no
wallet, order, position, balance, or key support. The adapter reads public
market data only, and only after you point it at markets you chose.

A selected outcome price is a share price in `[0, 1]`. It is a market price
proxy, not a calibrated probability. MarketPulse does not treat a Fed, election,
or other contract as bullish or bearish for a ticker unless your links file says
how you want that price compared. A divergence line is a heuristic prompt to
inspect both series. It is not causality, an edge, a profit claim, or a forecast.

Official references used by the client:

- Discovery and market metadata: [Gamma API](https://gamma-api.polymarket.com) and [Discover markets](https://docs.polymarket.com/market-data/discover-markets)
- Outcome, status, liquidity, and rules fields: [Market details](https://docs.polymarket.com/market-data/market-details)
- Public price history: [`https://data-api.polymarket.com/v2/prices-history`](https://data-api.polymarket.com/v2/prices-history) and [Prices and order books](https://docs.polymarket.com/market-data/prices-order-books)

### Discover a bounded list

Search does not modify the watchlist and does not guess a ticker.

```bash
.venv/bin/marketpulse polymarket-search --query "Fed" --limit 5
.venv/bin/marketpulse polymarket-search --market-id GAMMA_MARKET_ID
.venv/bin/marketpulse polymarket-search --slug market-or-event-slug
```

`--limit` is capped at 5 events. Each event shows at most 8 markets. One
`public-search` page is requested; if the provider sets `hasMore`, that fact is
printed and the extra page is not fetched, because that response does not
document a cursor. A 429 is reported and not retried.

Copy a real `market_id` and the exact outcome label into a YAML file. Do not
leave the example id in place if you want live data.

### Configure an explicit link

```yaml
links:
  - ticker: JPM
    market_id: "PASTE_GAMMA_ID"
    outcome: "Yes"
    relation: unspecified
    interpretation: >
      Yes means the contract's own question, which you should restate here.
      unspecified compares nothing directionally; it only shows the price
      beside JPM conversation.
```

`relation` is your comparison choice:

- `unspecified` — show the price. Never flag divergence.
- `higher_price_with_bullish` — a higher selected-outcome price is the side you asked to line up with bullish conversation.
- `higher_price_with_bearish` — a higher selected-outcome price is the side you asked to line up with bearish conversation.

The interpretation must be at least a short sentence. There is no packaged list of live markets.

### Run

```bash
.venv/bin/marketpulse --tickers JPM --polymarket polymarket.links.yml -o output
```

Environment alternatives: `MARKETPULSE_POLYMARKET` and `MARKETPULSE_POLYMARKET_FIXTURE`.
History snapshots go to `data/polymarket/observations.json` (atomic rewrite, idempotent
on `observation_id`). The same provider timestamp and price is not stored twice.
If no earlier observation exists inside the lookback, the window change stays empty.
MarketPulse does not invent a past price.

Public history, when a v1 `clobTokenIds` or v2 `positionIds` entry lines up with the
chosen outcome, is one or two GETs:

`https://data-api.polymarket.com/v2/prices-history?token_id=...&start=...&end=...&bucket_seconds=3600`

Those points are labeled `data_api_v2_prices_history`. `resolution_seconds: 0` is an
exact tick. Other widths are buckets, not a full trade tape. `--polymarket-skip-history`
skips that call. A history failure is a warning; Yahoo, Reddit, and X still finish.

The brief gains a Polymarket section only when the links file is set. `polymarket.json`
and `polymarket.csv` are the machine-readable copies. Divergence additionally
requires a directional recent tone with at least 3 mentions, a fresh Gamma
`updatedAt` (within 6 hours), `closed: false`, reported liquidity of at least
1000, and an aligned price move of at least 0.03 outside a dead zone. Missing
liquidity stays null. Volume in the table is Gamma `volume24hr` (trailing 24
hours, provider units, not converted).

### Offline demo

`examples/polymarket/` is synthetic. See `examples/polymarket/README.md`.

```bash
.venv/bin/marketpulse --tickers JPM \
  --polymarket examples/polymarket/links.example.yml \
  --polymarket-fixture examples/polymarket/fixture.json \
  --as-of 2026-11-09T18:00:00Z \
  -o output
```

`--as-of` is refused unless a fixture is present.

### What you still set up yourself

Live use needs network access to Gamma and the Data API. BigQuery export of the
new series needs the same GCP project, API, billing choice, dataset, and
Application Default Credentials described above, plus
`bq-init --bq-series polymarket` (or `all`). This repository does not log into
GCP or create those resources.

## Local Streamlit desk

The desk is an optional extra. Core `pip install -e '.[dev]'` and `pytest` do not
install or import Streamlit, and the desk never fetches Yahoo, Reddit, X,
Polymarket, or BigQuery. It only reads known files under the folder you choose:
`output/items.json`, `summary.json`, `polymarket.json`, `source_status.json`,
`alerts.json`, `prices.json`, `data/polymarket/observations.json`, and
`data/snapshots/*.json`.

Displayed clocks use **America/Cordoba (UTC-3)**. Mention filters use publication
time. Polymarket filters use `observed_at`. The mention-source filter does not
treat Polymarket as a news source. Snapshot files are listed as separate runs
and their volumes are not added to the mention count. Sentiment charts are the
mean of retained item scores. The saved recent/earlier window block stays the
radar's own aggregate. Outcome prices stay on a 0–1 scale, separate from
sentiment in [-1, 1]. Fewer than two price points produces an insufficient-history
message instead of a line. Missing scores stay `n/a`, not zero.

### Offline interview demo

Synthetic files, dated 7–9 Nov 2026 UTC, including synthetic Polymarket rows:

```bash
python examples/dashboard/build_demo.py
pip install -e '.[dashboard]'
python -m marketpulse.dashboard --demo
```

Open `http://127.0.0.1:8501`. The process binds to loopback only. The page is
labeled synthetic. It does not call Yahoo or Reddit.

### A radar run you already generated

```bash
python -m marketpulse --tickers JPM --polymarket polymarket.links.yml -o output --data-dir data
python -m marketpulse.dashboard
```

Use the sidebar to point at another folder that uses the same `output/` and
`data/` layout. Choose **Reload from disk** after a new radar run. That reload
reads files again; it does not call the sources. Generation itself can use the
network. The dashboard does not.

If Streamlit is missing, `python -m marketpulse.dashboard` exits 2 and prints
the install command above.

## Limitations and next steps

Lexicons miss sarcasm, novel slang, implied targets, conditional language and subject-level
relationships. Negating `fraud` can mechanically produce a positive score; it is not a
factual determination. Multi-ticker discussion is not reliably disentangled. Engagement,
author credibility, language identification and rigorous bot detection are not modeled.
Mentions retain neutralized spam, so even the volume measure is noisy. A bounded,
recent-only X selection provides no reliable historical volume baseline. Coverage and
provider access are not guaranteed. Holiday/weekend prices can be stale. Thin first-run
baselines must not be filled with invented data. Thresholds are not statistically tested;
there is no demonstrated accuracy, causality or prediction of returns. Good follow-up work:
manually labeled evaluation, entity-aware scoring, source-stratified coverage diagnostics,
and historical study with out-of-sample controls before drawing financial conclusions.

MIT License · Copyright 2026 Santos Linares.
