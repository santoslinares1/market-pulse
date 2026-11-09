-- Latest selected-outcome price versus the latest observation at or before a cutoff.
-- Replace YOUR_PROJECT, YOUR_DATASET, and the timestamps before running.
-- Set a maximum bytes billed. This query does not invent a baseline: if no earlier
-- row exists, earlier_price stays NULL.
--
-- The price difference is context for inspection. It is not an edge, a probability,
-- or evidence that conversation sentiment predicts the contract or the equity.

DECLARE recent_end TIMESTAMP DEFAULT TIMESTAMP("2026-11-09 18:00:00+00");
DECLARE recent_hours INT64 DEFAULT 6;
DECLARE lookback_hours INT64 DEFAULT 24;

WITH bounds AS (
  SELECT
    recent_end AS current_end,
    TIMESTAMP_SUB(recent_end, INTERVAL recent_hours HOUR) AS cutoff,
    TIMESTAMP_SUB(recent_end, INTERVAL lookback_hours HOUR) AS lookback_start
),
current_obs AS (
  SELECT
    ticker, market_id, outcome, series_id, price, observed_at, provenance, price_basis,
    volume_24h, liquidity, closed
  FROM `YOUR_PROJECT.YOUR_DATASET.polymarket_observations`, bounds
  WHERE observed_at > bounds.cutoff AND observed_at <= bounds.current_end
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY series_id ORDER BY observed_at DESC
  ) = 1
),
earlier_obs AS (
  SELECT ticker, series_id, price AS earlier_price, observed_at AS earlier_observed_at, provenance AS earlier_provenance
  FROM `YOUR_PROJECT.YOUR_DATASET.polymarket_observations`, bounds
  WHERE observed_at >= bounds.lookback_start AND observed_at <= bounds.cutoff
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY series_id ORDER BY observed_at DESC
  ) = 1
)
SELECT
  c.ticker,
  c.market_id,
  c.outcome,
  c.price AS current_price,
  c.observed_at AS current_observed_at,
  c.provenance AS current_provenance,
  c.price_basis,
  e.earlier_price,
  e.earlier_observed_at,
  e.earlier_provenance,
  IF(e.earlier_price IS NULL, NULL, c.price - e.earlier_price) AS price_delta,
  c.volume_24h,
  c.liquidity,
  c.closed
FROM current_obs AS c
LEFT JOIN earlier_obs AS e USING (series_id)
ORDER BY c.ticker, c.market_id;
