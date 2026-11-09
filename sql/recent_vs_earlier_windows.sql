-- Recent vs earlier window comparison in SQL (default-shaped 6h vs prior 18h of a 24h lookback).
-- Aligns with MarketPulse window ideas; does not imply price prediction or global volume.

DECLARE lookback_hours INT64 DEFAULT 24;
DECLARE recent_hours INT64 DEFAULT 6;

WITH bounded AS (
  SELECT *
  FROM `YOUR_PROJECT.YOUR_DATASET.mentions`
  WHERE published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL lookback_hours HOUR)
),
scored AS (
  SELECT
    ticker,
    source,
    CASE
      WHEN published_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL recent_hours HOUR)
        THEN 'recent'
      ELSE 'earlier'
    END AS window_name,
    score,
    label
  FROM bounded
)
SELECT
  ticker,
  window_name,
  COUNT(*) AS mentions,
  AVG(score) AS avg_score,
  COUNTIF(label = 'bullish') AS bullish,
  COUNTIF(label = 'bearish') AS bearish,
  COUNTIF(label = 'neutral') AS neutral
FROM scored
GROUP BY ticker, window_name
ORDER BY ticker, window_name;
