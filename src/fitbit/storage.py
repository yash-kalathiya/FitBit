"""DuckDB storage lifecycle and ingestion checkpoint management."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import Iterable, Iterator, Mapping, Sequence
from uuid import UUID, uuid4

import duckdb

from fitbit.config import DATABASE_FILE, PARQUET_DIR
from fitbit.data_types import DATA_TYPE_REGISTRY, get_data_type_spec


SCHEMA_VERSION = 3
LOCK_RETRY_DELAYS_SECONDS = (0.25, 0.5, 1.05, 2.0, 4.0)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_versions (
    component VARCHAR PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version > 0),
    applied_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id UUID PRIMARY KEY,
    data_type VARCHAR NOT NULL,
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    rows_written UBIGINT NOT NULL DEFAULT 0,
    error_message VARCHAR,
    started_at TIMESTAMP NOT NULL DEFAULT now(),
    finished_at TIMESTAMP,
    CHECK (window_start < window_end)
);

CREATE TABLE IF NOT EXISTS ingestion_state (
    data_type VARCHAR PRIMARY KEY,
    last_successful_end TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
"""


def _as_utc_naive(value: datetime) -> datetime:
    """Convert an aware datetime to the UTC representation stored by DuckDB."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ingestion window datetimes must be timezone-aware")
    return value.astimezone(UTC).replace(tzinfo=None)


class DuckDBStorage:
    """Own the local DuckDB file and ingestion bookkeeping transactions."""

    def __init__(
        self,
        database_file: Path = DATABASE_FILE,
        parquet_dir: Path = PARQUET_DIR,
    ) -> None:
        self.database_file = Path(database_file)
        self.parquet_dir = Path(parquet_dir)

    @contextmanager
    def connect(
        self,
        *,
        read_only: bool = False,
    ) -> Iterator[duckdb.DuckDBPyConnection]:
        """Open a short-lived connection and always close it."""
        if not read_only:
            self.database_file.parent.mkdir(parents=True, exist_ok=True)

        connection = None
        for attempt in range(len(LOCK_RETRY_DELAYS_SECONDS) + 1):
            try:
                connection = duckdb.connect(
                    str(self.database_file),
                    read_only=read_only,
                )
                break
            except duckdb.IOException as error:
                lock_conflict = "Could not set lock" in str(error)
                if not lock_conflict or attempt == len(LOCK_RETRY_DELAYS_SECONDS):
                    raise
                sleep(LOCK_RETRY_DELAYS_SECONDS[attempt])
        if connection is None:  # Defensive: the loop either connects or raises.
            raise RuntimeError("DuckDB connection retry ended unexpectedly")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create or migrate storage without replacing existing health data."""
        with self.connect() as connection:
            connection.execute(_SCHEMA_SQL)
            version_row = connection.execute(
                """
                SELECT version
                FROM schema_versions
                WHERE component = 'storage'
                """
            ).fetchone()
            current_version = version_row[0] if version_row else None
            if current_version is not None and current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {current_version} is newer than supported "
                    f"schema {SCHEMA_VERSION}"
                )

            connection.execute("BEGIN TRANSACTION")
            try:
                if current_version == 2:
                    self._migrate_v2_to_v3(connection)
                for spec in DATA_TYPE_REGISTRY.values():
                    connection.execute(spec.create_table_sql())
                connection.execute(
                    """
                    INSERT INTO schema_versions (component, version)
                    VALUES ('storage', ?)
                    ON CONFLICT (component) DO UPDATE
                    SET version = excluded.version,
                        applied_at = now()
                    """,
                    [SCHEMA_VERSION],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _migrate_v2_to_v3(connection: duckdb.DuckDBPyConnection) -> None:
        """Replace name primary keys with deterministic local storage keys."""
        for spec in DATA_TYPE_REGISTRY.values():
            columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = ?
                    """,
                    [spec.table_name],
                ).fetchall()
            }
            if not columns or "storage_key" in columns:
                continue

            migration_table = f"__migration_{spec.table_name}_v3"
            connection.execute(spec.create_table_sql(migration_table))
            old_columns = [
                column.name
                for column in spec.columns
                if column.name != "storage_key"
            ]
            quoted_old_columns = ", ".join(
                f'"{column}"' for column in old_columns
            )
            connection.execute(
                f"""
                INSERT INTO "{migration_table}" (
                    storage_key,
                    {quoted_old_columns}
                )
                SELECT
                    CASE
                        WHEN data_point_name <> ''
                            THEN 'google:' || data_point_name
                        ELSE 'legacy:' || md5(to_base64(raw_protobuf))
                    END,
                    {quoted_old_columns}
                FROM "{spec.table_name}"
                """
            )
            connection.execute(f'DROP TABLE "{spec.table_name}"')
            connection.execute(
                f'ALTER TABLE "{migration_table}" RENAME TO "{spec.table_name}"'
            )

    def upsert_rows(
        self,
        data_type: str,
        rows: Sequence[Mapping[str, object]],
    ) -> int:
        """Insert one bounded page and replace rows with the same local key."""
        if not rows:
            return 0

        spec = get_data_type_spec(data_type)
        columns = tuple(
            column.name for column in spec.columns if column.name != "ingested_at"
        )
        expected_columns = set(columns)
        for row in rows:
            if set(row) != expected_columns:
                missing = sorted(expected_columns - set(row))
                extra = sorted(set(row) - expected_columns)
                raise ValueError(
                    f"invalid {data_type} row columns; "
                    f"missing={missing}, extra={extra}"
                )

        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f'"{column}" = excluded."{column}"'
            for column in columns
            if column != "storage_key"
        )
        statement = f"""
            INSERT INTO "{spec.table_name}" ({quoted_columns})
            VALUES ({placeholders})
            ON CONFLICT (storage_key) DO UPDATE
            SET {updates}, ingested_at = now()
        """
        values = [[row[column] for column in columns] for row in rows]

        with self.connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.executemany(statement, values)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return len(rows)

    def export_parquet(
        self,
        data_types: Iterable[str] = DATA_TYPE_REGISTRY,
    ) -> dict[str, Path]:
        """Atomically refresh compact per-type Parquet analytics snapshots."""
        requested = tuple(data_types)
        specs = tuple(get_data_type_spec(data_type) for data_type in requested)
        if not specs:
            return {}

        self.parquet_dir.mkdir(parents=True, exist_ok=True)
        export_id = uuid4().hex
        temporary_files = {
            spec.api_name: self.parquet_dir
            / f".{spec.table_name}.{export_id}.parquet"
            for spec in specs
        }

        try:
            with self.connect(read_only=True) as connection:
                connection.execute("BEGIN TRANSACTION")
                try:
                    for spec in specs:
                        columns = [
                            column.name
                            for column in spec.columns
                            if column.name != "raw_protobuf"
                        ]
                        quoted_columns = ", ".join(
                            f'"{column}"' for column in columns
                        )
                        output_path = str(
                            temporary_files[spec.api_name].resolve()
                        ).replace("'", "''")
                        connection.execute(
                            f"""
                            COPY (
                                SELECT {quoted_columns}
                                FROM "{spec.table_name}"
                            ) TO '{output_path}' (
                                FORMAT PARQUET,
                                COMPRESSION ZSTD
                            )
                            """
                        )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise

            exported: dict[str, Path] = {}
            for spec in specs:
                destination = self.parquet_dir / f"{spec.table_name}.parquet"
                temporary_files[spec.api_name].replace(destination)
                exported[spec.api_name] = destination
            return exported
        finally:
            for temporary_file in temporary_files.values():
                temporary_file.unlink(missing_ok=True)

    def begin_run(
        self,
        data_type: str,
        window_start: datetime,
        window_end: datetime,
    ) -> UUID:
        """Record a bounded ingestion attempt and return its identifier."""
        if not data_type.strip():
            raise ValueError("data_type must not be empty")
        window_start_utc = _as_utc_naive(window_start)
        window_end_utc = _as_utc_naive(window_end)
        if window_start_utc >= window_end_utc:
            raise ValueError("window_start must be earlier than window_end")

        run_id = uuid4()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs (
                    run_id,
                    data_type,
                    window_start,
                    window_end,
                    status
                )
                VALUES (?, ?, ?, ?, 'running')
                """,
                [run_id, data_type, window_start_utc, window_end_utc],
            )
        return run_id

    def complete_run(self, run_id: UUID, rows_written: int) -> None:
        """Complete a run and advance its data-type checkpoint atomically."""
        if rows_written < 0:
            raise ValueError("rows_written must not be negative")

        with self.connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                run = connection.execute(
                    """
                    SELECT data_type, window_end
                    FROM ingestion_runs
                    WHERE run_id = ? AND status = 'running'
                    """,
                    [run_id],
                ).fetchone()
                if run is None:
                    raise ValueError(f"running ingestion run not found: {run_id}")

                data_type, window_end = run
                connection.execute(
                    """
                    UPDATE ingestion_runs
                    SET status = 'completed',
                        rows_written = ?,
                        finished_at = now()
                    WHERE run_id = ?
                    """,
                    [rows_written, run_id],
                )
                connection.execute(
                    """
                    INSERT INTO ingestion_state (data_type, last_successful_end)
                    VALUES (?, ?)
                    ON CONFLICT (data_type) DO UPDATE
                    SET last_successful_end = greatest(
                            ingestion_state.last_successful_end,
                            excluded.last_successful_end
                        ),
                        updated_at = now()
                    """,
                    [data_type, window_end],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def fail_run(self, run_id: UUID, error_message: str) -> None:
        """Mark a running ingestion attempt failed without advancing state."""
        with self.connect() as connection:
            run = connection.execute(
                """
                SELECT 1
                FROM ingestion_runs
                WHERE run_id = ? AND status = 'running'
                """,
                [run_id],
            ).fetchone()
            if run is None:
                raise ValueError(f"running ingestion run not found: {run_id}")

            connection.execute(
                """
                UPDATE ingestion_runs
                SET status = 'failed',
                    error_message = ?,
                    finished_at = now()
                WHERE run_id = ?
                """,
                [error_message, run_id],
            )

    def last_successful_end(self, data_type: str) -> datetime | None:
        """Return the durable checkpoint for a data type, if one exists."""
        with self.connect(read_only=True) as connection:
            row = connection.execute(
                """
                SELECT last_successful_end
                FROM ingestion_state
                WHERE data_type = ?
                """,
                [data_type],
            ).fetchone()
        return None if row is None else row[0].replace(tzinfo=UTC)
