-- Bullish / bearish / neutral trend by hour and by day (UTC).
-- These are retained sampled items, not total platform volume.

SELECT
  ticker,
  TIMESTAMP_TRUNC(published_at, HOUR) AS hour_utc,
  COUNTIF(label = 'bullish') AS bullish,
  COUNTIF(label = 'bearish') AS bearish,
  COUNTIF(label = 'neutral') AS neutral,
  COUNT(*) AS mentions
FROM `YOUR_PROJECT.YOUR_DATASET.mentions`
WHERE published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 48 HOUR)
GROUP BY ticker, hour_utc
ORDER BY ticker, hour_utc;

SELECT
  ticker,
  DATE(published_at) AS day_utc,
  COUNTIF(label = 'bullish') AS bullish,
  COUNTIF(label = 'bearish') AS bearish,
  COUNTIF(label = 'neutral') AS neutral,
  AVG(score) AS avg_score,
  COUNT(*) AS mentions
FROM `YOUR_PROJECT.YOUR_DATASET.mentions`
WHERE published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)
GROUP BY ticker, day_utc
ORDER BY ticker, day_utc;
