"""Bounded, resumable ingestion from Google Health into DuckDB."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from google.auth.credentials import Credentials
from google.devicesandservices.health.v4 import data_points_pb2

from fitbit.data_points import normalize_data_point
from fitbit.data_types import DATA_TYPE_REGISTRY, get_data_type_spec
from fitbit.health_client import iter_data_point_pages
from fitbit.storage import DuckDBStorage


DEFAULT_BACKFILL_DAYS = 120
DEFAULT_WINDOW_DAYS = 7
DEFAULT_OVERLAP_DAYS = 2

PageReader = Callable[
    [Credentials, str, datetime, datetime],
    Iterable[Sequence[data_points_pb2.DataPoint]],
]


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    data_type: str
    windows_completed: int
    rows_written: int


def iter_time_windows(
    start: datetime,
    end: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Iterator[tuple[datetime, datetime]]:
    """Split a range into adjacent half-open windows."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("backfill datetimes must be timezone-aware")
    if start >= end:
        raise ValueError("backfill start must be earlier than end")
    if window_days <= 0:
        raise ValueError("window_days must be positive")

    cursor = start
    width = timedelta(days=window_days)
    while cursor < end:
        window_end = min(cursor + width, end)
        yield cursor, window_end
        cursor = window_end


class IngestionEngine:
    """Coordinate per-type windows while keeping API and storage separate."""

    def __init__(
        self,
        storage: DuckDBStorage,
        page_reader: PageReader = iter_data_point_pages,
    ) -> None:
        self.storage = storage
        self.page_reader = page_reader

    def backfill(
        self,
        credentials: Credentials,
        *,
        end: datetime | None = None,
        lookback_days: int = DEFAULT_BACKFILL_DAYS,
        window_days: int = DEFAULT_WINDOW_DAYS,
        overlap_days: int = DEFAULT_OVERLAP_DAYS,
        data_types: Iterable[str] = DATA_TYPE_REGISTRY,
    ) -> tuple[IngestionSummary, ...]:
        """Backfill recent history or resume from each durable checkpoint."""
        if lookback_days <= 0:
            raise ValueError("lookback_days must be positive")
        if overlap_days < 0:
            raise ValueError("overlap_days must not be negative")

        requested_data_types = tuple(data_types)
        for data_type in requested_data_types:
            get_data_type_spec(data_type)

        range_end = end or datetime.now(UTC)
        if range_end.tzinfo is None or range_end.utcoffset() is None:
            raise ValueError("backfill end must be timezone-aware")
        earliest_start = range_end - timedelta(days=lookback_days)
        summaries: list[IngestionSummary] = []

        self.storage.initialize()
        for data_type in requested_data_types:
            checkpoint = self.storage.last_successful_end(data_type)
            range_start = earliest_start
            if checkpoint is not None:
                range_start = max(
                    earliest_start,
                    checkpoint - timedelta(days=overlap_days),
                )

            rows_written = 0
            windows_completed = 0
            for window_start, window_end in iter_time_windows(
                range_start,
                range_end,
                window_days,
            ):
                run_id = self.storage.begin_run(
                    data_type,
                    window_start,
                    window_end,
                )
                window_rows = 0
                try:
                    for page in self.page_reader(
                        credentials,
                        data_type,
                        window_start,
                        window_end,
                    ):
                        rows = [
                            normalize_data_point(data_type, data_point)
                            for data_point in page
                        ]
                        window_rows += self.storage.upsert_rows(data_type, rows)
                except Exception as error:
                    message = f"{type(error).__name__}: {error}"[:1_000]
                    try:
                        self.storage.fail_run(run_id, message)
                    except Exception as status_error:
                        error.add_note(
                            "could not persist failed ingestion status: "
                            f"{type(status_error).__name__}"
                        )
                    raise

                self.storage.complete_run(run_id, window_rows)
                rows_written += window_rows
                windows_completed += 1

            summaries.append(
                IngestionSummary(
                    data_type=data_type,
                    windows_completed=windows_completed,
                    rows_written=rows_written,
                )
            )
        self.storage.export_parquet(requested_data_types)
        return tuple(summaries)
