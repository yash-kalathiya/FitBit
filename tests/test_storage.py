"""Tests for the local DuckDB ingestion bookkeeping."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from fitbit.data_types import DATA_TYPE_REGISTRY, get_data_type_spec
from fitbit.storage import DuckDBStorage


class DuckDBStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_file = Path(self.temporary_directory.name) / "fitbit.duckdb"
        parquet_dir = Path(self.temporary_directory.name) / "parquet"
        self.storage = DuckDBStorage(database_file, parquet_dir)
        self.storage.initialize()
        self.window_start = datetime(2026, 9, 1, tzinfo=UTC)
        self.window_end = self.window_start + timedelta(days=7)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_initialize_creates_metadata_tables(self) -> None:
        with self.storage.connect(read_only=True) as connection:
            tables = {
                row[0]
                for row in connection.execute("SHOW TABLES").fetchall()
            }

        self.assertEqual(
            tables,
            {
                "daily_heart_rate_variability",
                "daily_resting_heart_rate",
                "daily_vo2_max",
                "exercise",
                "heart_rate",
                "ingestion_runs",
                "ingestion_state",
                "schema_versions",
                "sleep",
                "steps",
            },
        )

    def test_registry_maps_each_api_type_to_its_table(self) -> None:
        self.assertEqual(
            set(DATA_TYPE_REGISTRY),
            {
                "daily_heart_rate_variability",
                "daily_resting_heart_rate",
                "daily_vo2_max",
                "exercise",
                "heart_rate",
                "sleep",
                "steps",
            },
        )
        self.assertEqual(get_data_type_spec("sleep").table_name, "sleep")

    def test_unknown_data_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported data type"):
            get_data_type_spec("unknown")

    def test_health_tables_keep_typed_columns_and_raw_protobuf(self) -> None:
        expected_columns = {
            "daily_heart_rate_variability": {"average_hrv_milliseconds"},
            "daily_resting_heart_rate": {"beats_per_minute"},
            "daily_vo2_max": {"vo2_max"},
            "exercise": {"exercise_type", "active_duration_seconds"},
            "heart_rate": {"sample_time", "beats_per_minute"},
            "sleep": {"sleep_type", "minutes_asleep"},
            "steps": {"start_time", "end_time", "count"},
        }

        with self.storage.connect(read_only=True) as connection:
            for table_name, typed_columns in expected_columns.items():
                columns = {
                    row[0]: row[1]
                    for row in connection.execute(
                        f'DESCRIBE "{table_name}"'
                    ).fetchall()
                }
                self.assertEqual(columns["raw_protobuf"], "BLOB")
                self.assertEqual(columns["storage_key"], "VARCHAR")
                self.assertTrue(typed_columns <= columns.keys())

    def test_completed_run_advances_checkpoint(self) -> None:
        run_id = self.storage.begin_run(
            "sleep",
            self.window_start,
            self.window_end,
        )

        self.storage.complete_run(run_id, rows_written=4)

        self.assertEqual(
            self.storage.last_successful_end("sleep"),
            self.window_end,
        )
        with self.storage.connect(read_only=True) as connection:
            status, rows_written = connection.execute(
                """
                SELECT status, rows_written
                FROM ingestion_runs
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
        self.assertEqual(status, "completed")
        self.assertEqual(rows_written, 4)

    def test_export_parquet_creates_compact_per_type_snapshots(self) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO steps (
                    storage_key,
                    data_point_name,
                    raw_protobuf,
                    start_time,
                    end_time,
                    count
                )
                VALUES ('step-1', 'google-step', 'raw', ?, ?, 42)
                """,
                [self.window_start, self.window_end],
            )

        exported = self.storage.export_parquet(
            ("steps", "daily_heart_rate_variability")
        )

        self.assertEqual(
            set(exported),
            {"steps", "daily_heart_rate_variability"},
        )
        self.assertTrue(exported["steps"].is_file())
        self.assertTrue(
            exported["daily_heart_rate_variability"].is_file()
        )
        with duckdb.connect() as connection:
            steps = connection.read_parquet(str(exported["steps"]))
            self.assertEqual(steps.count("*").fetchone()[0], 1)
            self.assertNotIn("raw_protobuf", steps.columns)
            empty_hrv = connection.read_parquet(
                str(exported["daily_heart_rate_variability"])
            )
            self.assertEqual(empty_hrv.count("*").fetchone()[0], 0)

    def test_failed_run_does_not_advance_checkpoint(self) -> None:
        run_id = self.storage.begin_run(
            "sleep",
            self.window_start,
            self.window_end,
        )

        self.storage.fail_run(run_id, "synthetic failure")

        self.assertIsNone(self.storage.last_successful_end("sleep"))

    def test_begin_run_rejects_naive_datetimes(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.storage.begin_run(
                "sleep",
                self.window_start.replace(tzinfo=None),
                self.window_end,
            )

    def test_version_two_database_is_migrated_without_losing_rows(self) -> None:
        database_file = Path(self.temporary_directory.name) / "version-two.duckdb"
        current_spec = get_data_type_spec("steps")
        old_spec = replace(
            current_spec,
            columns=tuple(
                column
                for column in current_spec.columns
                if column.name != "storage_key"
            ),
        )
        with DuckDBStorage(database_file).connect() as connection:
            connection.execute(
                """
                CREATE TABLE schema_versions (
                    component VARCHAR PRIMARY KEY,
                    version INTEGER NOT NULL,
                    applied_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            )
            connection.execute(old_spec.create_table_sql())
            connection.execute(
                "INSERT INTO schema_versions VALUES ('storage', 2, now())"
            )
            connection.execute(
                """
                INSERT INTO steps (
                    data_point_name,
                    raw_protobuf,
                    start_time,
                    end_time,
                    count
                )
                VALUES ('google-name', 'raw', ?, ?, 42)
                """,
                [self.window_start, self.window_end],
            )

        migrated = DuckDBStorage(database_file)
        migrated.initialize()

        with migrated.connect(read_only=True) as connection:
            version = connection.execute(
                "SELECT version FROM schema_versions WHERE component = 'storage'"
            ).fetchone()[0]
            row = connection.execute(
                "SELECT storage_key, data_point_name, count FROM steps"
            ).fetchone()
        self.assertEqual(version, 3)
        self.assertEqual(row, ("google:google-name", "google-name", 42))


if __name__ == "__main__":
    unittest.main()
