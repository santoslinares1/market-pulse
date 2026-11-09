"""Offline BigQuery helpers: no network, optional dep may be absent."""
from datetime import datetime, timezone, timedelta
import json
import pytest

from marketpulse.bq import (
    BigQueryConfigError,
    BigQueryDependencyError,
    BigQueryExportError,
    BigQueryMentionStore,
    mention_id,
    mentions_ddl,
    merge_sql,
    parse_dataset_ref,
    require_identifier,
    require_project,
    row_from_item,
    rows_from_items,
)
from marketpulse.normalize import Item, SourceResult
from marketpulse import cli


def test_identifier_and_dataset_validation():
    assert require_identifier("mentions") == "mentions"
    assert require_project("demo-project") == "demo-project"
    assert parse_dataset_ref("demo-project.marketpulse") == ("demo-project", "marketpulse")
    with pytest.raises(BigQueryConfigError):
        require_identifier("mentions; drop")
    with pytest.raises(BigQueryConfigError):
        require_project("Bad_Project")
    with pytest.raises(BigQueryConfigError):
        parse_dataset_ref("onlyone")
    with pytest.raises(BigQueryConfigError):
        parse_dataset_ref("a.b.c")


def test_mention_id_stable_and_url_canonical():
    a = mention_id("aapl", "yahoo", "https://Example.com/x?utm_source=y")
    b = mention_id("AAPL", "yahoo", "https://example.com/x")
    assert a == b
    c = mention_id("AAPL", "yahoo", "https://example.com/x", external_id="99")
    assert c != a


def test_row_timestamp_conversion_utc(make_item, now):
    item = make_item(hours=2)
    item.signals = [{"signal": "raises guidance", "weight": 2, "negated": False}]
    item.flags = []
    row = row_from_item(
        item,
        run_id="run1",
        ingested_at=now,
        classifier_version="lexicon-1",
        lookback_hours=24,
        source_meta={"sampled": True, "fetched": 3, "accepted": 1, "status": "ok"},
    )
    assert row["published_at"].endswith("UTC")
    assert row["ingested_at"].endswith("UTC")
    assert row["ticker"] == "AAPL"
    assert json.loads(row["signals_json"])[0]["signal"] == "raises guidance"
    assert row["mention_id"] == mention_id(item.ticker, item.source, item.url)


def test_ddl_partition_cluster_and_merge_sql():
    ddl = mentions_ddl("demo-project", "marketpulse")
    assert "PARTITION BY DATE(published_at)" in ddl
    assert "CLUSTER BY ticker, source" in ddl
    assert "`demo-project.marketpulse.mentions`" in ddl
    sql = merge_sql("demo-project", "marketpulse", "mentions", "_mp_stage_abc")
    assert "MERGE `demo-project.marketpulse.mentions` T" in sql
    assert "ON T.mention_id = S.mention_id" in sql
    assert "WHEN MATCHED THEN UPDATE SET" in sql
    assert "classifier_version = S.classifier_version" in sql


class FakeJob:
    def __init__(self, output_rows=None, errors=None):
        self.output_rows = output_rows
        self.errors = errors

    def result(self):
        return self


class PermissionDenied(Exception):
    pass


class ServiceUnavailable(Exception):
    pass


class Unauthenticated(Exception):
    pass


class FakeGax:
    PermissionDenied = PermissionDenied
    ServiceUnavailable = ServiceUnavailable
    Unauthenticated = Unauthenticated
    NotFound = type("NotFound", (Exception,), {})


class FakeBQModule:
    class SchemaField:
        def __init__(self, name, field_type, mode="NULLABLE"):
            self.name, self.field_type, self.mode = name, field_type, mode

    class TimePartitioningType:
        DAY = "DAY"

    class TimePartitioning:
        def __init__(self, type_=None, field=None):
            self.type_, self.field = type_, field

    class Table:
        def __init__(self, table_id, schema=None):
            self.table_id = table_id
            self.full_table_id = table_id
            self.schema = schema
            self.time_partitioning = None
            self.clustering_fields = None

    class Dataset:
        def __init__(self, dataset_id):
            self.dataset_id = dataset_id
            self.location = None

        def __str__(self):
            return self.dataset_id

    class LoadJobConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class WriteDisposition:
        WRITE_APPEND = "WRITE_APPEND"

    class SourceFormat:
        NEWLINE_DELIMITED_JSON = "NEWLINE_DELIMITED_JSON"


class FakeClient:
    def __init__(self):
        self.created = []
        self.loaded = []
        self.queries = []
        self.deleted = []
        self.fail_mode = None

    def create_table(self, table, exists_ok=True):
        self.created.append(getattr(table, "full_table_id", str(table)))
        if self.fail_mode == "permission":
            raise PermissionDenied("denied")
        return table

    def create_dataset(self, dataset, exists_ok=True):
        self.created.append(str(dataset))
        return dataset

    def load_table_from_json(self, rows, table_id, job_config=None):
        if self.fail_mode == "network":
            raise ServiceUnavailable("unavailable")
        self.loaded.append((table_id, list(rows)))
        return FakeJob(output_rows=len(rows))

    def query(self, sql, location=None):
        self.queries.append((sql, location))
        return FakeJob(errors=None)

    def delete_table(self, table_id, not_found_ok=True):
        self.deleted.append(table_id)


def test_merge_rows_staging_idempotent(monkeypatch, make_item, now):
    import marketpulse.bq as bq

    fake_bq = FakeBQModule()
    monkeypatch.setattr(bq, "_import_bigquery", lambda: (fake_bq, FakeGax()))
    client = FakeClient()
    store = BigQueryMentionStore("demo-project", "marketpulse", client=client)
    store._bigquery = fake_bq
    store._gax = FakeGax()

    item = make_item()
    item.signals = []
    rows = rows_from_items([item, item], run_id="run-1", ingested_at=now, lookback_hours=24)
    assert rows[0]["mention_id"] == rows[1]["mention_id"]
    stats = store.merge_rows(rows, run_id="run-1")
    assert stats["attempted"] == 2
    assert stats["merged_batches"] == 1
    assert client.loaded and client.queries and client.deleted
    assert "MERGE" in client.queries[0][0]

    rows2 = rows_from_items(
        [item], run_id="run-2", ingested_at=now + timedelta(minutes=1),
        classifier_version="lexicon-2", lookback_hours=24,
    )
    assert rows2[0]["mention_id"] == rows[0]["mention_id"]
    assert rows2[0]["classifier_version"] == "lexicon-2"
    store.merge_rows(rows2, run_id="run-2")
    assert len(client.queries) == 2


def test_merge_incomplete_load_refuses(monkeypatch, make_item, now):
    import marketpulse.bq as bq

    fake_bq = FakeBQModule()
    monkeypatch.setattr(bq, "_import_bigquery", lambda: (fake_bq, FakeGax()))
    client = FakeClient()
    client.load_table_from_json = lambda rows, table_id, job_config=None: FakeJob(output_rows=0)
    store = BigQueryMentionStore("demo-project", "marketpulse", client=client)
    store._bigquery = fake_bq
    store._gax = FakeGax()
    rows = rows_from_items([make_item()], run_id="r", ingested_at=now)
    with pytest.raises(BigQueryExportError, match="incomplete batch"):
        store.merge_rows(rows, run_id="r")


def test_permission_and_network_errors_are_clear(monkeypatch, make_item, now):
    import marketpulse.bq as bq

    fake_bq = FakeBQModule()
    monkeypatch.setattr(bq, "_import_bigquery", lambda: (fake_bq, FakeGax()))
    client = FakeClient()
    client.fail_mode = "permission"
    store = BigQueryMentionStore("demo-project", "marketpulse", client=client)
    store._bigquery = fake_bq
    store._gax = FakeGax()
    with pytest.raises(BigQueryExportError, match="denied|Permission|IAM"):
        store.merge_rows(rows_from_items([make_item()], run_id="r", ingested_at=now), run_id="r")

    client2 = FakeClient()
    client2.fail_mode = "network"
    store2 = BigQueryMentionStore("demo-project", "marketpulse", client=client2)
    store2._bigquery = fake_bq
    store2._gax = FakeGax()
    with pytest.raises(BigQueryExportError, match="unavailable|Network|Service"):
        store2.merge_rows(rows_from_items([make_item()], run_id="r", ingested_at=now), run_id="r")


def test_optional_dependency_message():
    with pytest.raises(BigQueryDependencyError, match="marketpulse\\[bigquery\\]"):
        raise BigQueryDependencyError(
            "Optional BigQuery support requires: pip install 'marketpulse[bigquery]'."
        )


def _mock_collect(make_item):
    def collect(ticker, *args):
        # cli.main stamps the run with the wall clock, so the mention must sit inside that lookback.
        item = make_item(ticker=ticker, text=f"${ticker} raises guidance", hours=1)
        item.timestamp = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        return (
            [
                SourceResult(ticker, "yahoo", items=[item], fetched=1),
                SourceResult(ticker, "reddit", status="error", errors=["HTTPError: 403"]),
                SourceResult(ticker, "x", status="skipped", warnings=["No token"]),
                SourceResult(ticker, "yahoo_price", warnings=["Market closed"]),
            ],
            {"ticker": ticker, "percent_change": None, "usable_for_divergence": False, "note": "Stale"},
        )
    return collect


def test_cli_default_does_not_call_export(tmp_path, monkeypatch, make_item):
    monkeypatch.setattr(cli, "collect_ticker", _mock_collect(make_item))
    called = []

    def guard(*a, **k):
        called.append(True)
        raise AssertionError("export must not run by default")

    monkeypatch.setattr("marketpulse.bq.export_mentions", guard)
    code = cli.main(["--tickers", "AAPL", "-o", str(tmp_path / "out"), "--data-dir", str(tmp_path / "data")])
    assert code == 0
    assert called == []
    assert (tmp_path / "out" / "sentiment_brief.md").exists()


def test_cli_opt_in_bq_failure_preserves_local(tmp_path, monkeypatch, make_item):
    monkeypatch.setattr(cli, "collect_ticker", _mock_collect(make_item))

    def fail_export(*a, **k):
        raise BigQueryExportError("Unauthenticated: ADC missing. Run: gcloud auth application-default login")

    monkeypatch.setattr("marketpulse.bq.export_mentions", fail_export)
    out = tmp_path / "out"
    code = cli.main([
        "--tickers", "AAPL",
        "-o", str(out),
        "--data-dir", str(tmp_path / "data"),
        "--bq-dataset", "demo-project.marketpulse",
    ])
    assert code == cli.EXIT_BQ_FAILED
    assert (out / "sentiment_brief.md").exists()
    assert (out / "items.json").exists()


def test_cli_bq_ddl_no_cloud(capsys):
    code = cli.main(["bq-ddl", "--bq-dataset", "demo-project.marketpulse"])
    assert code == 0
    out = capsys.readouterr().out
    assert "CREATE TABLE IF NOT EXISTS `demo-project.marketpulse.mentions`" in out
    assert "PARTITION BY DATE(published_at)" in out


def test_cli_parser_has_bq_flags():
    args = cli.parser().parse_args([])
    assert args.bq_dataset is None or isinstance(args.bq_dataset, str)
    assert args.bq_table == "mentions"
    assert args.bq_location == "US"
