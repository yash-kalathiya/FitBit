"""Synthetic tests for pagination, normalization, and resumable backfill."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.protobuf.timestamp_pb2 import Timestamp
from google.type.date_pb2 import Date
from google.devicesandservices.health.v4 import data_coordinates_pb2
from google.devicesandservices.health.v4 import data_model_pb2
from google.devicesandservices.health.v4 import data_points_pb2

from fitbit.health_client import _iter_pages_from_stub, build_time_filter
from fitbit.ingestion import IngestionEngine, iter_time_windows
from fitbit.data_types import DATA_TYPE_REGISTRY
from fitbit.storage import DuckDBStorage


def _timestamp(value: datetime) -> Timestamp:
    result = Timestamp()
    result.FromDatetime(value)
    return result


def _steps_data_point(name: str, start: datetime) -> data_points_pb2.DataPoint:
    return data_points_pb2.DataPoint(
        name=name,
        steps=data_model_pb2.Steps(
            interval=data_coordinates_pb2.ObservationTimeInterval(
                start_time=_timestamp(start),
                end_time=_timestamp(start + timedelta(minutes=1)),
            ),
            count=12,
        ),
    )


def _point_for(data_type: str, start: datetime) -> data_points_pb2.DataPoint:
    interval = data_coordinates_pb2.SessionTimeInterval(
        start_time=_timestamp(start),
        end_time=_timestamp(start + timedelta(minutes=30)),
    )
    if data_type == "steps":
        return _steps_data_point("steps-point", start)
    if data_type == "heart_rate":
        return data_points_pb2.DataPoint(
            name="heart-rate-point",
            heart_rate=data_model_pb2.HeartRate(
                sample_time=data_coordinates_pb2.ObservationSampleTime(
                    physical_time=_timestamp(start)
                ),
                beats_per_minute=72,
            ),
        )
    if data_type == "daily_resting_heart_rate":
        return data_points_pb2.DataPoint(
            daily_resting_heart_rate=data_model_pb2.DailyRestingHeartRate(
                date=Date(year=start.year, month=start.month, day=start.day),
                beats_per_minute=58,
            )
        )
    if data_type == "daily_heart_rate_variability":
        return data_points_pb2.DataPoint(
            daily_heart_rate_variability=(
                data_model_pb2.DailyHeartRateVariability(
                    date=Date(year=start.year, month=start.month, day=start.day),
                    average_heart_rate_variability_milliseconds=48.0,
                )
            )
        )
    if data_type == "daily_vo2_max":
        return data_points_pb2.DataPoint(
            daily_vo2_max=data_model_pb2.DailyVO2Max(
                date=Date(year=start.year, month=start.month, day=start.day),
                vo2_max=44.0,
            )
        )
    if data_type == "sleep":
        return data_points_pb2.DataPoint(
            name="sleep-point",
            sleep=data_model_pb2.Sleep(interval=interval),
        )
    if data_type == "exercise":
        return data_points_pb2.DataPoint(
            name="exercise-point",
            exercise=data_model_pb2.Exercise(interval=interval),
        )
    raise AssertionError(f"unsupported synthetic data type: {data_type}")


class _FakeStub:
    def __init__(
        self,
        responses: list[data_points_pb2.ListDataPointsResponse],
    ) -> None:
        self.responses = iter(responses)
        self.requests = []

    def ListDataPoints(self, request, timeout):
        self.requests.append((request, timeout))
        return next(self.responses)


class HealthClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 9, 1, tzinfo=UTC)
        self.end = self.start + timedelta(days=1)

    def test_filters_match_each_google_coordinate_type(self) -> None:
        self.assertIn(
            "steps.interval.start_time",
            build_time_filter("steps", self.start, self.end),
        )
        self.assertIn(
            "heart_rate.sample_time.physical_time",
            build_time_filter("heart_rate", self.start, self.end),
        )
        self.assertIn(
            "sleep.interval.end_time",
            build_time_filter("sleep", self.start, self.end),
        )
        self.assertNotIn(
            "Z",
            build_time_filter("exercise", self.start, self.end),
        )
        self.assertEqual(
            build_time_filter("daily_vo2_max", self.start, self.end),
            'daily_vo2_max.date >= "2026-09-01" AND '
            'daily_vo2_max.date < "2026-09-02"',
        )

    def test_page_token_is_forwarded_unchanged(self) -> None:
        stub = _FakeStub([
            data_points_pb2.ListDataPointsResponse(next_page_token="opaque"),
            data_points_pb2.ListDataPointsResponse(),
        ])

        pages = list(
            _iter_pages_from_stub(stub, "heart_rate", self.start, self.end)
        )

        self.assertEqual(len(pages), 2)
        self.assertEqual(
            stub.requests[0][0].parent,
            "users/me/dataTypes/heart-rate",
        )
        self.assertEqual(stub.requests[0][0].page_size, 1_000)
        self.assertEqual(stub.requests[1][0].page_token, "opaque")

    def test_sleep_uses_its_documented_page_limit(self) -> None:
        stub = _FakeStub([data_points_pb2.ListDataPointsResponse()])

        list(_iter_pages_from_stub(stub, "sleep", self.start, self.end))

        self.assertEqual(stub.requests[0][0].page_size, 25)


class IngestionEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_file = Path(self.temporary_directory.name) / "fitbit.duckdb"
        parquet_dir = Path(self.temporary_directory.name) / "parquet"
        self.storage = DuckDBStorage(database_file, parquet_dir)
        self.end = datetime(2026, 9, 6, tzinfo=UTC)
        self.calls = []

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _page_reader(self, credentials, data_type, start, end):
        self.calls.append((data_type, start, end))
        yield [_steps_data_point(f"steps-{len(self.calls)}", start)]

    def test_backfill_creates_tables_writes_windows_and_checkpoints(self) -> None:
        summaries = IngestionEngine(
            self.storage,
            page_reader=self._page_reader,
        ).backfill(
            object(),
            end=self.end,
            lookback_days=10,
            window_days=7,
            data_types=("steps",),
        )

        self.assertEqual(len(self.calls), 2)
        self.assertEqual(summaries[0].windows_completed, 2)
        self.assertEqual(summaries[0].rows_written, 2)
        self.assertEqual(self.storage.last_successful_end("steps"), self.end)
        with self.storage.connect(read_only=True) as connection:
            count = connection.execute("SELECT count(*) FROM steps").fetchone()[0]
        self.assertEqual(count, 2)
        self.assertTrue((self.storage.parquet_dir / "steps.parquet").is_file())

    def test_upsert_makes_an_overlapping_retry_idempotent(self) -> None:
        point = _steps_data_point("stable-name", self.end - timedelta(days=1))
        row_reader = lambda credentials, data_type, start, end: ([point],)
        engine = IngestionEngine(self.storage, page_reader=row_reader)

        for _ in range(2):
            engine.backfill(
                object(),
                end=self.end,
                lookback_days=1,
                data_types=("steps",),
            )

        with self.storage.connect(read_only=True) as connection:
            count = connection.execute("SELECT count(*) FROM steps").fetchone()[0]
        self.assertEqual(count, 1)

    def test_anonymous_point_uses_stable_local_identity(self) -> None:
        start = self.end - timedelta(hours=1)
        original = _steps_data_point("", start)
        corrected = _steps_data_point("", start)
        corrected.steps.count = 18
        responses = iter(([original], [corrected]))

        def page_reader(credentials, data_type, window_start, window_end):
            return (next(responses),)

        engine = IngestionEngine(self.storage, page_reader=page_reader)
        for _ in range(2):
            engine.backfill(
                object(),
                end=self.end,
                lookback_days=1,
                data_types=("steps",),
            )

        with self.storage.connect(read_only=True) as connection:
            rows = connection.execute(
                "SELECT data_point_name, count FROM steps"
            ).fetchall()
        self.assertEqual(rows, [(None, 18)])

    def test_all_registered_types_normalize_into_their_tables(self) -> None:
        def page_reader(credentials, data_type, start, end):
            return ([_point_for(data_type, start)],)

        IngestionEngine(self.storage, page_reader=page_reader).backfill(
            object(),
            end=self.end,
            lookback_days=1,
        )

        with self.storage.connect(read_only=True) as connection:
            for table_name in DATA_TYPE_REGISTRY:
                count = connection.execute(
                    f'SELECT count(*) FROM "{table_name}"'
                ).fetchone()[0]
                self.assertEqual(count, 1, table_name)

    def test_windows_are_adjacent_and_end_exactly_at_requested_time(self) -> None:
        start = self.end - timedelta(days=10)
        windows = list(iter_time_windows(start, self.end, window_days=7))

        self.assertEqual(windows, [
            (start, start + timedelta(days=7)),
            (start + timedelta(days=7), self.end),
        ])


if __name__ == "__main__":
    unittest.main()
