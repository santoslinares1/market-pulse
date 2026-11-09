-- Dedup / history checks: mention_id uniqueness and rescore provenance.
-- MERGE keeps one row per mention_id; reruns update score/label/classifier_version/run_id.

-- Should return 0 rows if MERGE identity is healthy.
SELECT mention_id, COUNT(*) AS row_count
FROM `YOUR_PROJECT.YOUR_DATASET.mentions`
GROUP BY mention_id
HAVING COUNT(*) > 1;

-- Latest ingest activity by classifier version (rescored rows show newest ingested_at).
SELECT
  classifier_version,
  COUNT(*) AS mentions,
  MIN(published_at) AS oldest_published,
  MAX(ingested_at) AS newest_ingest
FROM `YOUR_PROJECT.YOUR_DATASET.mentions`
GROUP BY classifier_version
ORDER BY newest_ingest DESC;

-- Same URL seen across runs would share mention_id; inspect recent updates.
SELECT mention_id, ticker, source, url, score, label, classifier_version, run_id, published_at, ingested_at
FROM `YOUR_PROJECT.YOUR_DATASET.mentions`
WHERE ingested_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY)
ORDER BY ingested_at DESC
LIMIT 100;
