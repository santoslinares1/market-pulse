-- Counts by ticker and source (bounded sampled mentions only).
-- Replace YOUR_PROJECT.YOUR_DATASET. Optional: set a maximum bytes billed in the UI/CLI.

SELECT
  ticker,
  source,
  COUNT(*) AS mention_count,
  COUNTIF(label = 'bullish') AS bullish,
  COUNTIF(label = 'bearish') AS bearish,
  COUNTIF(label = 'neutral') AS neutral,
  AVG(score) AS avg_score
FROM `YOUR_PROJECT.YOUR_DATASET.mentions`
WHERE published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
GROUP BY ticker, source
ORDER BY ticker, source;
