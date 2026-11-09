-- MarketPulse Polymarket observation warehouse (manual / bq-init --bq-series polymarket).
-- Replace YOUR_PROJECT and YOUR_DATASET before running. This does not alter `mentions`.
--
-- Identity: observation_id = SHA256 of series_id|observed_at|price_basis|price|provenance.
-- series_id = SHA256 of market_id|outcome label.
-- Idempotency: MERGE on observation_id. Price and observed_at stay in the key.
-- A repeated read of the same provider timestamp and price does not insert a second row.
--
-- Prices are Gamma outcome share prices or Data API v2 history points in [0, 1].
-- They are market price proxies, not calibrated probabilities, causal effects, or trading signals.
-- volume_24h is Gamma volume24hr (trailing 24h, provider units, not converted).
-- liquidity is Gamma liquidity (unit not named by the field; missing stays NULL).

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT.YOUR_DATASET.polymarket_observations` (
  observation_id STRING NOT NULL,
  series_id STRING NOT NULL,
  ticker STRING NOT NULL,
  market_id STRING NOT NULL,
  slug STRING,
  outcome STRING NOT NULL,
  question STRING,
  price FLOAT64 NOT NULL,
  price_basis STRING NOT NULL,
  observed_at TIMESTAMP NOT NULL,
  observed_at_basis STRING NOT NULL,
  ingested_at TIMESTAMP NOT NULL,
  provenance STRING NOT NULL,
  sampling STRING,
  volume_24h FLOAT64,
  liquidity FLOAT64,
  closed BOOL,
  resolution_status STRING,
  run_id STRING NOT NULL,
  source_url STRING
)
PARTITION BY DATE(observed_at)
CLUSTER BY ticker, market_id;
