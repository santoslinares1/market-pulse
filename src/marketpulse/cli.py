"""Run the radar once; write local, inspectable evidence and atomic snapshots."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import io
import math
import os
from pathlib import Path
import sys
import uuid
import requests
import yaml
from .aggregate import analyze
from .classify import classify_item
from .normalize import Item, select
from .report import render
from .sources import yahoo, reddit, x
from .storage import PROJECT_ROOT, atomic_text, load_history, save_json
from .watchlist import load_watchlist, symbols

# Exit 3 = local artifacts written, opt-in BigQuery export failed.
EXIT_BQ_FAILED = 3


def parser():
    p = argparse.ArgumentParser(
        description="MarketPulse: conversation shifts, not price predictions.",
        epilog=(
            "BigQuery helpers: marketpulse bq-ddl | bq-init | bq-export | bq-export-polymarket. "
            "Polymarket discovery: marketpulse polymarket-search. See README. "
            "Default runs do not call BigQuery or Polymarket."
        ),
    )
    p.add_argument("--hours", type=float, default=24, help="Lookback hours (default 24; maximum 168)")
    p.add_argument("--recent-hours", type=float, default=6, help="Recent comparison window (default 6)")
    p.add_argument("--tickers", help="Comma-separated tickers; replaces default watchlist for this run")
    p.add_argument("--watchlist", type=Path, help="YAML path; otherwise project root / env override")
    p.add_argument("-o", "--output", type=Path, default=PROJECT_ROOT / "output", help="Artifact directory")
    p.add_argument("--x-file", type=Path, help="Imported X posts JSON; takes precedence over X API")
    p.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data", help="Persistent snapshot directory")
    p.add_argument(
        "--bq-dataset",
        default=os.environ.get("MARKETPULSE_BQ_DATASET"),
        help="Opt-in BigQuery export as project.dataset (or env MARKETPULSE_BQ_DATASET). "
        "Default runs never call BigQuery.",
    )
    p.add_argument(
        "--bq-location",
        default=os.environ.get("MARKETPULSE_BQ_LOCATION", "US"),
        help="BigQuery location for opt-in export/init (default US / MARKETPULSE_BQ_LOCATION)",
    )
    p.add_argument(
        "--bq-table",
        default=os.environ.get("MARKETPULSE_BQ_TABLE", "mentions"),
        help="Mentions table id inside the dataset (default mentions)",
    )
    p.add_argument(
        "--polymarket",
        type=Path,
        default=None,
        help="Opt-in YAML linking tickers to explicit Polymarket markets. "
        "Unset (and without MARKETPULSE_POLYMARKET) the radar does not call Polymarket.",
    )
    p.add_argument(
        "--polymarket-fixture",
        type=Path,
        default=None,
        help="Offline Gamma-shaped JSON. When set, Polymarket is not contacted.",
    )
    p.add_argument(
        "--polymarket-skip-history",
        action="store_true",
        help="Do not read public price history for the selected outcome token.",
    )
    p.add_argument(
        "--as-of",
        default=None,
        help="UTC timestamp for an offline fixture demo only. Rejected for live source runs.",
    )
    return p


def collect_ticker(ticker, now, hours, recent_hours, payload, import_error):
    with requests.Session() as session:
        results = [yahoo.fetch_news(ticker, now, hours, session),
                   reddit.fetch_posts(ticker, now, hours, session),
                   x.fetch_posts(ticker, now, hours, session, payload=payload, import_error=import_error)]
        price, price_result = yahoo.fetch_price(ticker, now, recent_hours, session)
        return results + [price_result], price


def _polymarket_paths(args):
    config = args.polymarket
    if config is None and os.environ.get("MARKETPULSE_POLYMARKET"):
        config = Path(os.environ["MARKETPULSE_POLYMARKET"]).expanduser()
    fixture = args.polymarket_fixture
    if fixture is None and os.environ.get("MARKETPULSE_POLYMARKET_FIXTURE"):
        fixture = Path(os.environ["MARKETPULSE_POLYMARKET_FIXTURE"]).expanduser()
    return config, fixture


def run(args, now=None):
    now = now or datetime.now(timezone.utc)
    poly_config, poly_fixture_path = _polymarket_paths(args)
    poly_links = poly_fixture = None
    if poly_config or poly_fixture_path:
        from .polymarket import PolymarketConfigError, load_fixture, load_links
        if not poly_config:
            raise ValueError("A Polymarket fixture requires --polymarket (the explicit links file)")
        try:
            poly_links = load_links(poly_config)
            poly_fixture = load_fixture(poly_fixture_path) if poly_fixture_path else None
        except PolymarketConfigError as exc:
            raise ValueError(str(exc)) from exc
    watchlist = symbols(args.tickers.split(",")) if args.tickers else load_watchlist(args.watchlist).symbols
    if not watchlist:
        raise ValueError("At least one ticker is required")
    x_path = args.x_file or (Path(os.environ["MARKETPULSE_X_JSON"]).expanduser() if os.environ.get("MARKETPULSE_X_JSON") else None)
    payload, import_error = None, None
    if x_path:
        try:
            payload = x.load_import(x_path)
        except (OSError, ValueError) as exc:
            import_error = f"{type(exc).__name__}: {exc}"
    snapshots = args.data_dir / "snapshots"
    history, warnings = load_history(snapshots, now, args.recent_hours)
    items, statuses, prices = [], [], {}
    # Independent ticker jobs; one Reddit request per ticker, no retry storms or block bypasses.
    with ThreadPoolExecutor(max_workers=3) as executor:
        batches = executor.map(lambda ticker: collect_ticker(ticker, now, args.hours, args.recent_hours, payload, import_error), watchlist)
        for ticker, (results, price) in zip(watchlist, batches):
            statuses.extend(r.as_status() for r in results)
            items.extend(classify_item(item) for r in results for item in r.items)
            prices[ticker] = price
    items = select(items, now, args.hours)
    for status in statuses:
        if status["source"] != "yahoo_price":
            status["accepted"] = sum(i.ticker == status["ticker"] and i.source == status["source"] for i in items)
    run_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    result = {"schema_version": 1, "classifier_version": "lexicon-1", "generated_at": now.isoformat(),
              "run_id": run_id, "hours": args.hours, "recent_hours": args.recent_hours, "warnings": warnings,
              "source_status": statuses, "tickers": {}}
    for ticker in watchlist:
        result["tickers"][ticker] = analyze(ticker, items, now, args.hours, args.recent_hours,
                                             prices[ticker], statuses, history)
    poly_rows = []
    if poly_links:
        from .polymarket import run_polymarket, write_polymarket_files
        history_path = args.data_dir / "polymarket" / "observations.json"
        kwargs = dict(
            links=poly_links, watchlist=watchlist, fixture=poly_fixture, now=now,
            hours=args.hours, recent_hours=args.recent_hours, history_path=history_path,
            run_id=run_id, ticker_results=result["tickers"],
            read_history=not args.polymarket_skip_history,
        )
        if poly_fixture is None:
            with requests.Session() as session:
                poly_payload, poly_statuses, poly_rows = run_polymarket(session=session, **kwargs)
        else:
            poly_payload, poly_statuses, poly_rows = run_polymarket(session=None, **kwargs)
        result["polymarket"] = poly_payload
        result["warnings"] = list(result["warnings"]) + list(poly_payload.get("warnings") or [])
        statuses.extend(poly_statuses)
        for report in poly_payload.get("links") or []:
            flag = report.get("divergence_flag")
            ticker = report.get("ticker")
            if flag and ticker in result["tickers"]:
                result["tickers"][ticker]["alerts"].append(flag)
    output = args.output
    save_json(output / "items.json", [asdict(i) for i in items])
    save_json(output / "summary.json", result)
    save_json(output / "source_status.json", statuses)
    save_json(output / "alerts.json", [a for d in result["tickers"].values() for a in d["alerts"]])
    save_json(output / "prices.json", prices)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["ticker", "source", "timestamp", "label", "score", "url", "text", "signals", "flags"])
    writer.writeheader()
    for item in items:
        record = asdict(item)
        record["text"] = record["text"][:1000]
        record["signals"] = "; ".join(s["signal"] + (" [negated]" if s["negated"] else "") for s in item.signals)
        record["flags"] = "; ".join(item.flags)
        # CSV is evidence for spreadsheets, not an opportunity for formula injection.
        for key, value in record.items():
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                record[key] = "'" + value
        writer.writerow(record)
    atomic_text(output / "items.csv", buffer.getvalue())
    atomic_text(output / "sentiment_brief.md", render(result, items))
    if poly_links:
        write_polymarket_files(output, result["polymarket"])
    save_json(snapshots / f"{run_id}.json", result)
    result["_items"] = items  # in-memory only for optional BQ export; not written to snapshot
    result["_polymarket_rows"] = poly_rows
    return result


def cmd_bq_ddl(argv):
    from .bq import mentions_ddl, parse_dataset_ref, polymarket_ddl, BigQueryConfigError

    p = argparse.ArgumentParser(prog="marketpulse bq-ddl")
    p.add_argument(
        "--bq-dataset",
        default=os.environ.get("MARKETPULSE_BQ_DATASET", "your-project.your_dataset"),
        help="project.dataset used only to render DDL identifiers (no cloud call)",
    )
    p.add_argument("--bq-table", default=os.environ.get("MARKETPULSE_BQ_TABLE", "mentions"))
    p.add_argument(
        "--bq-series",
        choices=("mentions", "polymarket", "all"),
        default="mentions",
        help="mentions (default, unchanged) | polymarket observations | both. No cloud call.",
    )
    p.add_argument(
        "--bq-polymarket-table",
        default=os.environ.get("MARKETPULSE_BQ_POLYMARKET_TABLE", "polymarket_observations"),
    )
    args = p.parse_args(argv)
    try:
        project, dataset = parse_dataset_ref(args.bq_dataset)
        chunks = []
        if args.bq_series in {"mentions", "all"}:
            chunks.append(mentions_ddl(project, dataset, args.bq_table))
        if args.bq_series in {"polymarket", "all"}:
            chunks.append(polymarket_ddl(project, dataset, args.bq_polymarket_table))
        print("\n".join(chunks), end="")
    except BigQueryConfigError as exc:
        print(f"MarketPulse: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_bq_init(argv):
    from .bq import (
        BigQueryConfigError,
        BigQueryDependencyError,
        BigQueryExportError,
        BigQueryMentionStore,
        parse_dataset_ref,
    )

    p = argparse.ArgumentParser(prog="marketpulse bq-init")
    p.add_argument("--bq-dataset", default=os.environ.get("MARKETPULSE_BQ_DATASET"), required=False)
    p.add_argument("--bq-location", default=os.environ.get("MARKETPULSE_BQ_LOCATION", "US"))
    p.add_argument("--bq-table", default=os.environ.get("MARKETPULSE_BQ_TABLE", "mentions"))
    p.add_argument("--bq-series", choices=("mentions", "polymarket", "all"), default="mentions")
    p.add_argument(
        "--bq-polymarket-table",
        default=os.environ.get("MARKETPULSE_BQ_POLYMARKET_TABLE", "polymarket_observations"),
    )
    args = p.parse_args(argv)
    if not args.bq_dataset:
        print("MarketPulse: bq-init requires --bq-dataset project.dataset", file=sys.stderr)
        return 2
    try:
        project, dataset = parse_dataset_ref(args.bq_dataset)
        if args.bq_series in {"mentions", "all"}:
            store = BigQueryMentionStore(
                project, dataset, table=args.bq_table, location=args.bq_location
            )
            print(store.ddl(), end="")
            store.ensure_dataset_and_table(create_dataset=True)
            print(f"Ensured {store.table_id} in location {args.bq_location}", file=sys.stderr)
        if args.bq_series in {"polymarket", "all"}:
            from .bq import BigQueryObservationStore
            observed = BigQueryObservationStore(
                project, dataset, table=args.bq_polymarket_table, location=args.bq_location
            )
            print(observed.ddl(), end="")
            observed.ensure_dataset_and_table(create_dataset=args.bq_series == "polymarket")
            print(f"Ensured {observed.table_id} in location {args.bq_location}", file=sys.stderr)
    except BigQueryConfigError as exc:
        print(f"MarketPulse: {exc}", file=sys.stderr)
        return 2
    except BigQueryDependencyError as exc:
        print(f"MarketPulse: {exc}", file=sys.stderr)
        return 2
    except BigQueryExportError as exc:
        print(f"MarketPulse BigQuery: {exc}", file=sys.stderr)
        return EXIT_BQ_FAILED
    return 0


def cmd_bq_export(argv):
    """Export an existing local items.json via staging MERGE (no refetch)."""
    from .bq import (
        BigQueryConfigError,
        BigQueryDependencyError,
        BigQueryExportError,
        export_mentions,
    )

    p = argparse.ArgumentParser(prog="marketpulse bq-export")
    p.add_argument("--items", type=Path, required=True, help="Path to items.json from a prior run")
    p.add_argument("--bq-dataset", default=os.environ.get("MARKETPULSE_BQ_DATASET"))
    p.add_argument("--bq-location", default=os.environ.get("MARKETPULSE_BQ_LOCATION", "US"))
    p.add_argument("--bq-table", default=os.environ.get("MARKETPULSE_BQ_TABLE", "mentions"))
    p.add_argument("--run-id", default=None, help="Provenance run id (default: generated)")
    p.add_argument("--hours", type=float, default=None, help="Optional lookback_hours provenance")
    p.add_argument("--classifier-version", default="lexicon-1")
    args = p.parse_args(argv)
    if not args.bq_dataset:
        print("MarketPulse: bq-export requires --bq-dataset project.dataset", file=sys.stderr)
        return 2
    try:
        import json
        payload = json.loads(args.items.read_text(encoding="utf-8"))
        items = [
            Item(
                ticker=row["ticker"],
                source=row["source"],
                timestamp=row["timestamp"],
                url=row["url"],
                text=row["text"],
                label=row.get("label", "neutral"),
                score=float(row.get("score", 0.0)),
                signals=row.get("signals") or [],
                flags=row.get("flags") or [],
            )
            for row in payload
        ]
        run_id = args.run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
        stats = export_mentions(
            items,
            dataset_ref=args.bq_dataset,
            run_id=run_id,
            classifier_version=args.classifier_version,
            lookback_hours=args.hours,
            location=args.bq_location,
            table=args.bq_table,
        )
        print(f"BigQuery MERGE attempted={stats['attempted']} batches={stats['merged_batches']} run_id={run_id}")
    except (OSError, ValueError, KeyError, TypeError, BigQueryConfigError) as exc:
        print(f"MarketPulse: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except BigQueryDependencyError as exc:
        print(f"MarketPulse: {exc}", file=sys.stderr)
        return 2
    except BigQueryExportError as exc:
        print(f"MarketPulse BigQuery: {exc}", file=sys.stderr)
        return EXIT_BQ_FAILED
    return 0


def cmd_bq_export_polymarket(argv):
    """Export a local observations.json via staging MERGE. No refetch and no query."""
    from .bq import (
        BigQueryConfigError,
        BigQueryDependencyError,
        BigQueryExportError,
        export_polymarket_observations,
    )

    p = argparse.ArgumentParser(prog="marketpulse bq-export-polymarket")
    p.add_argument("--observations", type=Path, required=True, help="data/polymarket/observations.json")
    p.add_argument("--bq-dataset", default=os.environ.get("MARKETPULSE_BQ_DATASET"))
    p.add_argument("--bq-location", default=os.environ.get("MARKETPULSE_BQ_LOCATION", "US"))
    p.add_argument(
        "--bq-polymarket-table",
        default=os.environ.get("MARKETPULSE_BQ_POLYMARKET_TABLE", "polymarket_observations"),
    )
    p.add_argument("--run-id", default=None)
    args = p.parse_args(argv)
    if not args.bq_dataset:
        print("MarketPulse: bq-export-polymarket requires --bq-dataset project.dataset", file=sys.stderr)
        return 2
    try:
        import json
        payload = json.loads(args.observations.read_text(encoding="utf-8"))
        rows = payload.get("observations") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("observations file must contain an observations list")
        run_id = args.run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
        stats = export_polymarket_observations(
            rows, dataset_ref=args.bq_dataset, run_id=run_id,
            location=args.bq_location, table=args.bq_polymarket_table,
        )
        print(
            f"BigQuery polymarket MERGE attempted={stats['attempted']} "
            f"batches={stats['merged_batches']} run_id={run_id}"
        )
    except (OSError, ValueError, KeyError, TypeError, BigQueryConfigError) as exc:
        print(f"MarketPulse: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except BigQueryDependencyError as exc:
        print(f"MarketPulse: {exc}", file=sys.stderr)
        return 2
    except BigQueryExportError as exc:
        print(f"MarketPulse BigQuery: {exc}", file=sys.stderr)
        return EXIT_BQ_FAILED
    return 0


def _maybe_export_bq(args, result):
    dataset = getattr(args, "bq_dataset", None)
    if not dataset:
        return 0
    from .bq import BigQueryDependencyError, BigQueryExportError, BigQueryConfigError, export_mentions

    items = result.get("_items") or []
    try:
        stats = export_mentions(
            items,
            dataset_ref=dataset,
            run_id=result["run_id"],
            ingested_at=datetime.fromisoformat(result["generated_at"].replace("Z", "+00:00")),
            classifier_version=result.get("classifier_version", "lexicon-1"),
            lookback_hours=result.get("hours"),
            source_statuses=result.get("source_status"),
            location=getattr(args, "bq_location", "US"),
            table=getattr(args, "bq_table", "mentions"),
        )
    except BigQueryConfigError as exc:
        print(f"MarketPulse BigQuery config: {exc}", file=sys.stderr)
        return EXIT_BQ_FAILED
    except BigQueryDependencyError as exc:
        print(f"MarketPulse: {exc}", file=sys.stderr)
        return EXIT_BQ_FAILED
    except BigQueryExportError as exc:
        print(f"MarketPulse BigQuery: {exc}", file=sys.stderr)
        return EXIT_BQ_FAILED
    print(
        f"BigQuery MERGE ok attempted={stats['attempted']} batches={stats['merged_batches']} "
        f"→ {dataset}.{getattr(args, 'bq_table', 'mentions')}"
    )
    rows = result.get("_polymarket_rows") or []
    if not rows:
        return 0
    from .bq import export_polymarket_observations
    table = os.environ.get("MARKETPULSE_BQ_POLYMARKET_TABLE", "polymarket_observations")
    try:
        poly_stats = export_polymarket_observations(
            rows,
            dataset_ref=dataset,
            run_id=result["run_id"],
            location=getattr(args, "bq_location", "US"),
            table=table,
        )
    except BigQueryConfigError as exc:
        print(f"MarketPulse BigQuery config: {exc}", file=sys.stderr)
        return EXIT_BQ_FAILED
    except BigQueryDependencyError as exc:
        print(f"MarketPulse: {exc}", file=sys.stderr)
        return EXIT_BQ_FAILED
    except BigQueryExportError as exc:
        print(f"MarketPulse BigQuery: {exc}", file=sys.stderr)
        return EXIT_BQ_FAILED
    print(
        f"BigQuery polymarket MERGE ok attempted={poly_stats['attempted']} "
        f"batches={poly_stats['merged_batches']} → {dataset}.{table}"
    )
    return 0


def cmd_polymarket_search(argv):
    """Bounded public discovery. Prints JSON and does not change the watchlist."""
    from .polymarket import PolymarketConfigError, discover_markets
    from .sources.polymarket import PolymarketRateLimit

    p = argparse.ArgumentParser(prog="marketpulse polymarket-search")
    p.add_argument("--query", default=None, help="Free-text public-search query, for example Fed")
    p.add_argument("--market-id", default=None, help="Fetch one Gamma market id")
    p.add_argument("--slug", default=None, help="Fetch one market slug, then an event slug on 404")
    p.add_argument("--limit", type=int, default=5, help="Local cap, 1 to 5 events")
    args = p.parse_args(argv)
    chosen = [bool(args.query), bool(args.market_id), bool(args.slug)]
    if sum(chosen) != 1:
        print("MarketPulse: polymarket-search needs exactly one of --query, --market-id, --slug", file=sys.stderr)
        return 2
    try:
        with requests.Session() as session:
            document = discover_markets(
                session, query=args.query, market_id=args.market_id, slug=args.slug, limit=args.limit,
            )
    except PolymarketConfigError as exc:
        print(f"MarketPulse: {exc}", file=sys.stderr)
        return 2
    except (requests.RequestException, PolymarketRateLimit, ValueError) as exc:
        print(f"MarketPulse: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    import json
    print(json.dumps(document, indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "bq-ddl":
        return cmd_bq_ddl(argv[1:])
    if argv and argv[0] == "bq-init":
        return cmd_bq_init(argv[1:])
    if argv and argv[0] == "bq-export":
        return cmd_bq_export(argv[1:])
    if argv and argv[0] == "bq-export-polymarket":
        return cmd_bq_export_polymarket(argv[1:])
    if argv and argv[0] == "polymarket-search":
        return cmd_polymarket_search(argv[1:])

    p = parser()
    args = p.parse_args(argv)
    if not (math.isfinite(args.hours) and math.isfinite(args.recent_hours)
            and 0 < args.recent_hours < args.hours <= 168):
        p.error("Require 0 < recent-hours < hours <= 168 (finite values)")
    frozen = None
    if args.as_of:
        if not (args.polymarket_fixture or os.environ.get("MARKETPULSE_POLYMARKET_FIXTURE")):
            print("MarketPulse: --as-of is only available with an offline --polymarket-fixture", file=sys.stderr)
            return 2
        try:
            frozen = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
        except ValueError:
            print("MarketPulse: --as-of must be an ISO-8601 timestamp", file=sys.stderr)
            return 2
        if frozen.tzinfo is None:
            print("MarketPulse: --as-of must include a timezone", file=sys.stderr)
            return 2
        frozen = frozen.astimezone(timezone.utc)
        if frozen > datetime.now(timezone.utc) + timedelta(minutes=1):
            print("MarketPulse: --as-of cannot be in the future", file=sys.stderr)
            return 2
    try:
        result = run(args, now=frozen)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"MarketPulse: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    available = sum(d["overall"]["volume"] for d in result["tickers"].values())
    problems = sum(s["status"] != "ok" for s in result["source_status"])
    print(f"MarketPulse wrote {args.output.resolve() / 'sentiment_brief.md'}")
    print(f"{available} ticker-item mentions; {problems} source statuses need attention. Not a price prediction.")
    bq_code = _maybe_export_bq(args, result)
    if bq_code:
        return bq_code
    return 0 if available else 1
