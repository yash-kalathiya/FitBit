"""DuckDB storage lifecycle and ingestion checkpoint management."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

import duckdb

from fitbit.config import DATABASE_FILE
from fitbit.data_types import DATA_TYPE_REGISTRY


SCHEMA_VERSION = 2

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

    def __init__(self, database_file: Path = DATABASE_FILE) -> None:
        self.database_file = Path(database_file)

    @contextmanager
    def connect(self, *, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
        """Open a short-lived connection and always close it."""
        if not read_only:
            self.database_file.parent.mkdir(parents=True, exist_ok=True)

        connection = duckdb.connect(str(self.database_file), read_only=read_only)
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create storage metadata tables without replacing existing data."""
        with self.connect() as connection:
            connection.execute(_SCHEMA_SQL)
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
