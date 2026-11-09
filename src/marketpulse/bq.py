"""Optional BigQuery mention history: schema, validated identifiers, staging MERGE.

Default MarketPulse runs never import google.cloud or touch the network for BQ.
Use Application Default Credentials only — no API keys or credential files in-repo.

Idempotency is MERGE on mention_id via a staging table, not streaming insertId
best-effort dedupe. When the same mention is seen again (or the classifier
version changes), MERGE updates score/label/signals/flags/classifier_version/
ingested_at/run_id/provenance while keeping mention_id and published_at stable.
This is current-state upsert history, not a full score audit log.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .normalize import Item, canonical_url, parse_time

# Keep table/dataset identifiers narrow: letters, digits, underscore.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")  # GCP project ids: 6–30 chars

TABLE_MENTIONS = "mentions"
BATCH_SIZE = 500
CLASSIFIER_DEFAULT = "lexicon-1"

MENTIONS_SCHEMA_FIELDS = (
    ("mention_id", "STRING", "REQUIRED"),
    ("ticker", "STRING", "REQUIRED"),
    ("source", "STRING", "REQUIRED"),
    ("external_id", "STRING", "NULLABLE"),
    ("url", "STRING", "NULLABLE"),
    ("published_at", "TIMESTAMP", "REQUIRED"),
    ("ingested_at", "TIMESTAMP", "REQUIRED"),
    ("text", "STRING", "NULLABLE"),
    ("score", "FLOAT64", "NULLABLE"),
    ("label", "STRING", "NULLABLE"),
    ("signals_json", "STRING", "NULLABLE"),
    ("flags_json", "STRING", "NULLABLE"),
    ("classifier_version", "STRING", "REQUIRED"),
    ("run_id", "STRING", "REQUIRED"),
    ("lookback_hours", "FLOAT64", "NULLABLE"),
    ("source_sampled", "BOOL", "NULLABLE"),
    ("source_fetched", "INT64", "NULLABLE"),
    ("source_accepted", "INT64", "NULLABLE"),
    ("source_status", "STRING", "NULLABLE"),
)


class BigQueryConfigError(ValueError):
    """Invalid project/dataset/table/location configuration."""


class BigQueryDependencyError(RuntimeError):
    """Optional google-cloud-bigquery extra is not installed."""


class BigQueryExportError(RuntimeError):
    """Authenticated / network / permission failure during opt-in export."""


def require_identifier(name: str, *, kind: str = "identifier") -> str:
    if not isinstance(name, str) or not _IDENT.match(name):
        raise BigQueryConfigError(
            f"Invalid BigQuery {kind} {name!r}: use letters, digits, underscore; "
            "must start with a letter or underscore."
        )
    return name


def require_project(project: str) -> str:
    if not isinstance(project, str) or not _PROJECT.match(project) or "--" in project:
        raise BigQueryConfigError(
            f"Invalid GCP project id {project!r}. "
            "Expected a standard project id (letters, digits, hyphens)."
        )
    return project


def parse_dataset_ref(value: str) -> tuple[str, str]:
    """Parse 'project.dataset' (exactly two dotted parts)."""
    if not isinstance(value, str) or value.count(".") != 1:
        raise BigQueryConfigError(
            "BigQuery dataset must be 'project.dataset' (exactly one dot)."
        )
    project, dataset = value.split(".", 1)
    return require_project(project), require_identifier(dataset, kind="dataset")


def fq_table(project: str, dataset: str, table: str) -> str:
    require_project(project)
    require_identifier(dataset, kind="dataset")
    require_identifier(table, kind="table")
    return f"`{project}.{dataset}.{table}`"


def mention_id(ticker: str, source: str, url: str, external_id: str | None = None) -> str:
    """Stable identity: ticker + source + external_id (preferred) or canonical URL."""
    ticker_n = str(ticker).strip().upper()
    source_n = str(source).strip().lower()
    if external_id:
        key = f"{ticker_n}|{source_n}|id:{external_id.strip()}"
    else:
        key = f"{ticker_n}|{source_n}|url:{canonical_url(url)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _iso_utc(value: datetime | str) -> str:
    if isinstance(value, str):
        value = parse_time(value)
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f UTC")


def row_from_item(
    item: Item,
    *,
    run_id: str,
    ingested_at: datetime,
    classifier_version: str,
    lookback_hours: float | None,
    source_meta: Mapping[str, Any] | None = None,
    external_id: str | None = None,
) -> dict[str, Any]:
    meta = source_meta or {}
    published = parse_time(item.timestamp)
    return {
        "mention_id": mention_id(item.ticker, item.source, item.url, external_id),
        "ticker": item.ticker.upper(),
        "source": item.source,
        "external_id": external_id,
        "url": item.url,
        "published_at": _iso_utc(published),
        "ingested_at": _iso_utc(ingested_at),
        "text": item.text,
        "score": float(item.score),
        "label": item.label,
        "signals_json": json.dumps(item.signals, ensure_ascii=False, allow_nan=False),
        "flags_json": json.dumps(item.flags, ensure_ascii=False, allow_nan=False),
        "classifier_version": classifier_version,
        "run_id": run_id,
        "lookback_hours": lookback_hours,
        "source_sampled": meta.get("sampled"),
        "source_fetched": meta.get("fetched"),
        "source_accepted": meta.get("accepted"),
        "source_status": meta.get("status"),
    }


def rows_from_items(
    items: Sequence[Item],
    *,
    run_id: str,
    ingested_at: datetime,
    classifier_version: str = CLASSIFIER_DEFAULT,
    lookback_hours: float | None = None,
    source_statuses: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    index: dict[tuple[str, str], Mapping[str, Any]] = {}
    for status in source_statuses or ():
        index[(status.get("ticker", ""), status.get("source", ""))] = status
    rows = []
    for item in items:
        meta = index.get((item.ticker, item.source), {})
        rows.append(
            row_from_item(
                item,
                run_id=run_id,
                ingested_at=ingested_at,
                classifier_version=classifier_version,
                lookback_hours=lookback_hours,
                source_meta=meta,
            )
        )
    return rows


def mentions_ddl(project: str, dataset: str, table: str = TABLE_MENTIONS) -> str:
    """DDL only — does not create resources. Partitioned by published_at date."""
    parts = []
    for name, typ, mode in MENTIONS_SCHEMA_FIELDS:
        suffix = " NOT NULL" if mode == "REQUIRED" else ""
        parts.append(f"{name} {typ}{suffix}")
    cols = ",\n  ".join(parts)
    target = fq_table(project, dataset, table)
    return (
        f"CREATE TABLE IF NOT EXISTS {target} (\n  {cols}\n)\n"
        f"PARTITION BY DATE(published_at)\n"
        f"CLUSTER BY ticker, source;\n"
    )


def merge_sql(project: str, dataset: str, table: str, staging: str) -> str:
    target = fq_table(project, dataset, table)
    stage = fq_table(project, dataset, staging)
    # Update classification + provenance on match; keep published_at / mention_id.
    return f"""
MERGE {target} T
USING {stage} S
ON T.mention_id = S.mention_id
WHEN MATCHED THEN UPDATE SET
  ticker = S.ticker,
  source = S.source,
  external_id = S.external_id,
  url = S.url,
  ingested_at = S.ingested_at,
  text = S.text,
  score = S.score,
  label = S.label,
  signals_json = S.signals_json,
  flags_json = S.flags_json,
  classifier_version = S.classifier_version,
  run_id = S.run_id,
  lookback_hours = S.lookback_hours,
  source_sampled = S.source_sampled,
  source_fetched = S.source_fetched,
  source_accepted = S.source_accepted,
  source_status = S.source_status
WHEN NOT MATCHED THEN INSERT (
  mention_id, ticker, source, external_id, url, published_at, ingested_at,
  text, score, label, signals_json, flags_json, classifier_version, run_id,
  lookback_hours, source_sampled, source_fetched, source_accepted, source_status
) VALUES (
  S.mention_id, S.ticker, S.source, S.external_id, S.url, S.published_at, S.ingested_at,
  S.text, S.score, S.label, S.signals_json, S.flags_json, S.classifier_version, S.run_id,
  S.lookback_hours, S.source_sampled, S.source_fetched, S.source_accepted, S.source_status
)
""".strip()


def _import_bigquery():
    try:
        from google.cloud import bigquery  # type: ignore
        from google.api_core import exceptions as gax_exceptions  # type: ignore
    except ImportError as exc:
        raise BigQueryDependencyError(
            "Optional BigQuery support requires: pip install 'marketpulse[bigquery]'. "
            "Default MarketPulse installs do not include google-cloud-bigquery."
        ) from exc
    return bigquery, gax_exceptions


def _schema_fields(bigquery):
    mode_map = {"REQUIRED": "REQUIRED", "NULLABLE": "NULLABLE"}
    return [
        bigquery.SchemaField(name, typ, mode=mode_map[mode])
        for name, typ, mode in MENTIONS_SCHEMA_FIELDS
    ]


def _classify_rpc_error(exc: Exception, gax_exceptions) -> BigQueryExportError:
    name = type(exc).__name__
    message = str(exc)
    if isinstance(exc, getattr(gax_exceptions, "Unauthenticated", ())):
        tip = "ADC missing or expired. Run: gcloud auth application-default login"
    elif isinstance(exc, getattr(gax_exceptions, "PermissionDenied", ())):
        tip = "IAM denied. Need bigquery.jobs.create, dataEditor on dataset, and read session."
    elif isinstance(exc, getattr(gax_exceptions, "NotFound", ())):
        tip = "Project/dataset/table not found. Run: marketpulse bq-init --bq-dataset project.dataset"
    elif isinstance(exc, getattr(gax_exceptions, "ServiceUnavailable", ())) or "timed out" in message.lower():
        tip = "Network/service unavailable. Retry later; local reports were still written."
    else:
        tip = "BigQuery request failed; local reports were still written if export was opt-in after the run."
    return BigQueryExportError(f"{name}: {message}. {tip}")


class BigQueryMentionStore:
    """Thin wrapper around google.cloud.bigquery.Client for init + staging MERGE."""

    def __init__(
        self,
        project: str,
        dataset: str,
        *,
        table: str = TABLE_MENTIONS,
        location: str = "US",
        client: Any | None = None,
    ):
        self.project = require_project(project)
        self.dataset = require_identifier(dataset, kind="dataset")
        self.table = require_identifier(table, kind="table")
        self.location = str(location or "US")
        if client is not None:
            self.client = client
            self._bigquery = None
            self._gax = None
        else:
            bigquery, gax = _import_bigquery()
            self._bigquery = bigquery
            self._gax = gax
            try:
                self.client = bigquery.Client(project=self.project, location=self.location)
            except Exception as exc:  # noqa: BLE001 — surface as export/auth error
                raise _classify_rpc_error(exc, gax) from exc

    @property
    def table_id(self) -> str:
        return f"{self.project}.{self.dataset}.{self.table}"

    def ddl(self) -> str:
        return mentions_ddl(self.project, self.dataset, self.table)

    def ensure_dataset_and_table(self, *, create_dataset: bool = True) -> None:
        """Explicit init only — never called from the default radar path."""
        bigquery = self._bigquery
        gax = self._gax
        if bigquery is None:
            bigquery, gax = _import_bigquery()
            self._bigquery, self._gax = bigquery, gax
        try:
            dataset_ref = bigquery.Dataset(f"{self.project}.{self.dataset}")
            dataset_ref.location = self.location
            if create_dataset:
                self.client.create_dataset(dataset_ref, exists_ok=True)
            table = bigquery.Table(self.table_id, schema=_schema_fields(bigquery))
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="published_at",
            )
            table.clustering_fields = ["ticker", "source"]
            self.client.create_table(table, exists_ok=True)
        except Exception as exc:  # noqa: BLE001
            raise _classify_rpc_error(exc, gax) from exc

    def merge_rows(self, rows: Sequence[Mapping[str, Any]], *, run_id: str) -> dict[str, int]:
        if not rows:
            return {"attempted": 0, "merged_batches": 0}
        bigquery = self._bigquery
        gax = self._gax
        if bigquery is None:
            bigquery, gax = _import_bigquery()
            self._bigquery, self._gax = bigquery, gax
        # Unique staging table per run; deleted after MERGE.
        staging_name = require_identifier(
            f"_mp_stage_{re.sub(r'[^A-Za-z0-9_]', '_', run_id)[:80]}",
            kind="table",
        )
        staging_id = f"{self.project}.{self.dataset}.{staging_name}"
        attempted = 0
        batches = 0
        try:
            staging = bigquery.Table(staging_id, schema=_schema_fields(bigquery))
            self.client.create_table(staging, exists_ok=True)
            for start in range(0, len(rows), BATCH_SIZE):
                chunk = [dict(r) for r in rows[start : start + BATCH_SIZE]]
                # load_table_from_json is a load job — reliable vs streaming insertId.
                job_config = bigquery.LoadJobConfig(
                    schema=_schema_fields(bigquery),
                    write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                    source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                )
                load_job = self.client.load_table_from_json(chunk, staging_id, job_config=job_config)
                result = load_job.result()
                output_rows = getattr(result, "output_rows", None)
                if output_rows is not None and output_rows != len(chunk):
                    raise BigQueryExportError(
                        f"Staging load wrote {output_rows} rows but {len(chunk)} were sent; "
                        "refusing to MERGE with incomplete batch."
                    )
                attempted += len(chunk)
                batches += 1
            merge = merge_sql(self.project, self.dataset, self.table, staging_name)
            query_job = self.client.query(merge, location=self.location)
            query_job.result()
            if getattr(query_job, "errors", None):
                raise BigQueryExportError(f"MERGE reported errors: {query_job.errors}")
        except BigQueryExportError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _classify_rpc_error(exc, gax) from exc
        finally:
            try:
                self.client.delete_table(staging_id, not_found_ok=True)
            except Exception:  # noqa: BLE001 — cleanup best-effort; MERGE may have succeeded
                pass
        return {"attempted": attempted, "merged_batches": batches}


def export_mentions(
    items: Sequence[Item],
    *,
    dataset_ref: str,
    run_id: str,
    ingested_at: datetime | None = None,
    classifier_version: str = CLASSIFIER_DEFAULT,
    lookback_hours: float | None = None,
    source_statuses: Sequence[Mapping[str, Any]] | None = None,
    location: str = "US",
    table: str = TABLE_MENTIONS,
    client: Any | None = None,
) -> dict[str, int]:
    project, dataset = parse_dataset_ref(dataset_ref)
    store = BigQueryMentionStore(project, dataset, table=table, location=location, client=client)
    rows = rows_from_items(
        items,
        run_id=run_id,
        ingested_at=ingested_at or datetime.now(timezone.utc),
        classifier_version=classifier_version,
        lookback_hours=lookback_hours,
        source_statuses=source_statuses,
    )
    return store.merge_rows(rows, run_id=run_id)
