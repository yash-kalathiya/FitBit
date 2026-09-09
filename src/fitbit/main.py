"""Command-line entry point for local Fitbit ingestion."""

from __future__ import annotations

import argparse

from fitbit.auth import get_credentials
from fitbit.data_types import DATA_TYPE_REGISTRY
from fitbit.ingestion import DEFAULT_BACKFILL_DAYS, IngestionEngine
from fitbit.storage import DuckDBStorage


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fitbit")
    commands = parser.add_subparsers(dest="command", required=True)
    backfill = commands.add_parser(
        "backfill",
        help="store recent Google Health history in the local DuckDB database",
    )
    backfill.add_argument("--days", type=int, default=DEFAULT_BACKFILL_DAYS)
    backfill.add_argument(
        "--data-type",
        action="append",
        choices=tuple(DATA_TYPE_REGISTRY),
        dest="data_types",
        help="limit the run; repeat for more than one type",
    )
    export = commands.add_parser(
        "export-parquet",
        help="rebuild per-type Parquet snapshots from the local DuckDB database",
    )
    export.add_argument(
        "--data-type",
        action="append",
        choices=tuple(DATA_TYPE_REGISTRY),
        dest="data_types",
        help="limit the export; repeat for more than one type",
    )
    return parser


def main() -> None:
    """Run an explicit command without printing personal health records."""
    arguments = _parser().parse_args()
    if arguments.command == "backfill":
        summaries = IngestionEngine(DuckDBStorage()).backfill(
            get_credentials(),
            lookback_days=arguments.days,
            data_types=arguments.data_types or DATA_TYPE_REGISTRY,
        )
        for summary in summaries:
            print(
                f"{summary.data_type}: {summary.windows_completed} windows, "
                f"{summary.rows_written} rows processed"
            )
    elif arguments.command == "export-parquet":
        storage = DuckDBStorage()
        storage.initialize()
        exported = storage.export_parquet(
            arguments.data_types or DATA_TYPE_REGISTRY
        )
        for data_type, path in exported.items():
            print(f"{data_type}: {path}")


if __name__ == "__main__":
    main()
