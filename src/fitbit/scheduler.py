"""Run bounded Google Health ingestion twice daily inside the sidecar."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

from fitbit.auth import get_credentials
from fitbit.ingestion import IngestionEngine
from fitbit.storage import DuckDBStorage


LOGGER = logging.getLogger("fitbit.scheduler")
DEFAULT_SCHEDULE = "08:00,20:00"
DEFAULT_LOOKBACK_DAYS = 7


def parse_schedule(value: str) -> tuple[tuple[int, int], ...]:
    """Parse a comma-separated local-time schedule."""
    parsed: list[tuple[int, int]] = []
    for item in value.split(","):
        try:
            hour_text, minute_text = item.strip().split(":", maxsplit=1)
            hour, minute = int(hour_text), int(minute_text)
        except ValueError as error:
            raise ValueError("schedule entries must use HH:MM") from error
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("schedule entries must be valid local times")
        parsed.append((hour, minute))
    if not parsed:
        raise ValueError("at least one ingestion time is required")
    return tuple(sorted(set(parsed)))


def seconds_until_next_run(
    now: datetime,
    schedule: tuple[tuple[int, int], ...],
) -> float:
    """Return seconds until the next configured wall-clock time."""
    candidates = [
        now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for hour, minute in schedule
    ]
    future = [candidate for candidate in candidates if candidate > now]
    next_run = min(future) if future else min(candidates) + timedelta(days=1)
    return max((next_run - now).total_seconds(), 0.0)


def ingest_once(lookback_days: int) -> None:
    """Run one private ingestion without printing health values."""
    summaries = IngestionEngine(DuckDBStorage()).backfill(
        get_credentials(interactive=False),
        lookback_days=lookback_days,
    )
    LOGGER.info(
        "ingestion completed: %s",
        ", ".join(
            f"{summary.data_type}={summary.rows_written} rows"
            for summary in summaries
        ),
    )


def main() -> None:
    """Run once at startup, then at the configured local times forever."""
    logging.basicConfig(
        level=os.environ.get("FITBIT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    schedule = parse_schedule(
        os.environ.get("FITBIT_INGEST_TIMES", DEFAULT_SCHEDULE)
    )
    lookback_days = int(
        os.environ.get("FITBIT_INGEST_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)
    )
    if lookback_days <= 0:
        raise ValueError("FITBIT_INGEST_LOOKBACK_DAYS must be positive")

    run_on_start = os.environ.get("FITBIT_INGEST_RUN_ON_START", "true").lower()
    if run_on_start in {"1", "true", "yes"}:
        try:
            ingest_once(lookback_days)
        except Exception:
            LOGGER.exception("startup ingestion failed; the schedule will continue")

    while True:
        delay = seconds_until_next_run(datetime.now().astimezone(), schedule)
        LOGGER.info("next ingestion in %.1f hours", delay / 3_600)
        time.sleep(delay)
        try:
            ingest_once(lookback_days)
        except Exception:
            LOGGER.exception("scheduled ingestion failed; the schedule will continue")
        time.sleep(1)


if __name__ == "__main__":
    main()
