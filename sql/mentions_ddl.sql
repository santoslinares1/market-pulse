-- MarketPulse mentions warehouse (manual / bq-init).
-- Replace YOUR_PROJECT and YOUR_DATASET before running in the BigQuery console.
-- Time-partitioned by published_at (UTC calendar day); clustered by ticker, source.
--
-- Identity: mention_id = SHA256 of ticker|source|url-or-external-id.
-- Idempotency: MERGE upserts on mention_id (staging load + MERGE), not streaming insertId.
-- When a mention is rescored or classifier_version changes, score/label/signals update;
-- published_at and mention_id stay stable. This is current-state history, not a full score audit log.
--
-- Sampled Yahoo / Reddit / X feeds are bounded observations, NOT estimates of global
-- conversation volume. Scores are lexicon labels, not predictive performance.

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT.YOUR_DATASET.mentions` (
  mention_id STRING NOT NULL,
  ticker STRING NOT NULL,
  source STRING NOT NULL,
  external_id STRING,
  url STRING,
  published_at TIMESTAMP NOT NULL,
  ingested_at TIMESTAMP NOT NULL,
  text STRING,
  score FLOAT64,
  label STRING,
  signals_json STRING,
  flags_json STRING,
  classifier_version STRING NOT NULL,
  run_id STRING NOT NULL,
  lookback_hours FLOAT64,
  source_sampled BOOL,
  source_fetched INT64,
  source_accepted INT64,
  source_status STRING
)
PARTITION BY DATE(published_at)
CLUSTER BY ticker, source;
