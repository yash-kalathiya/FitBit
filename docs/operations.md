# Local operations

## Services

`docker compose up --build -d` starts:

- `dashboard`: read-only Rust analytics presented by Streamlit at
  `http://localhost:8080`;
- `ingestion`: a Python sidecar that runs at startup, 08:00, and 20:00 local
  time using a seven-day lookback and the durable two-day overlap checkpoint.

The morning run captures sleep and overnight physiology after device sync. The
20:00 run captures most daytime activity while leaving the dashboard current
before the night.

## Private mounts

Compose mounts `./db` into both services and `./.secrets` only into ingestion.
Both paths are excluded by Git and the Docker build context. The secrets mount
is writable because OAuth refresh can rotate token metadata. Never commit or
copy either directory into an image.

Initial browser authorization must happen on the host:

```bash
PYTHONPATH="$PWD/generated" uv run fitbit backfill --days 1
```

## Analytics snapshots

Every successful ingestion refreshes the selected files under `db/parquet/`.
The dashboard reads those files through the Rust engine; it does not attach the
transactional DuckDB file. To rebuild all snapshots from DuckDB without calling
Google:

```bash
PYTHONPATH="$PWD/generated" uv run fitbit export-parquet
```

The export writes temporary files first and atomically replaces each completed
snapshot. If ingestion fails, the previous snapshots remain available.

## Schedule configuration

```bash
TZ=America/Los_Angeles \
FITBIT_INGEST_TIMES=08:00,20:00 \
docker compose up -d
```

The sidecar catches a failed run and waits for the next scheduled time instead
of exiting. Inspect aggregate operational logs with `docker compose logs
ingestion`; the application does not log individual records.

## DuckDB editor connection

When an editor cannot open the file directly, attach the path as a quoted SQL
literal:

```sql
ATTACH '/home/yash/Project/FitBit/db/fitbit.duckdb'
AS fitbit (READ_ONLY);
SHOW TABLES FROM fitbit;
```

Use `fitbit.main.TABLE_NAME` for qualified queries. A `Catalog "home" does not
exist` error means `/home/...` was parsed as an identifier. A lock error means
ingestion is writing or another editor connection is still attached; normal
dashboard reads no longer lock this file.
