# Fitbit local analytics

A lightweight, local-first analytics project built on authorized Google Health
API data from a Fitbit device. Python ingests bounded gRPC pages into DuckDB, a
derived per-type Parquet layer feeds a read-only Rust engine, and Streamlit
presents only compact analytical results. Docker Compose runs the dashboard and
a twice-daily ingestion sidecar locally.

## Repository layout

```text
src/fitbit/       Hand-written Python application code
generated/        Generated Google Health protobuf/gRPC modules
rust/              Read-only Parquet metrics engine
docs/             Architecture and operational design documents
db/                Private DuckDB source and Parquet snapshots (ignored by Git)
.secrets/         Local OAuth configuration and tokens (ignored by Git)
AGENTS.md         Project-specific collaboration and safety guidance
```

## Native setup and initial backfill

Install the locked Python environment, then explicitly authorize and backfill
the available Fitbit history:

```bash
uv sync --locked
PYTHONPATH="$PWD/generated" uv run fitbit backfill --days 120
```

The command never prints individual health records. It processes one API page
at a time, deduplicates stable records, and resumes with a two-day overlap for
late Fitbit synchronization. After a successful run it atomically replaces one
analytics snapshot per selected type under `db/parquet/`.

DuckDB remains the authoritative database. Rebuild every analytics snapshot
without calling Google:

```bash
PYTHONPATH="$PWD/generated" uv run fitbit export-parquet
```

## Run the local application

The container build compiles the Rust engine and installs the Python dashboard.
Neither `.secrets/` nor `db/` is copied into the image; Compose mounts them at
runtime.

```bash
docker compose up --build -d
docker compose ps
```

Open <http://localhost:8080>. The `ingestion` sidecar runs once at startup and
then at 08:00 and 20:00 in `${TZ:-America/Los_Angeles}`. Override the times with
`FITBIT_INGEST_TIMES=07:30,19:30`. The sidecar needs an already-authorized token;
perform the initial browser authorization on the host.

To stop both services:

```bash
docker compose down
```

## Inspect DuckDB safely

Open `db/fitbit.duckdb` directly in a DuckDB-aware editor, or attach the absolute
path as a quoted SQL string:

```sql
ATTACH '/home/yash/Project/FitBit/db/fitbit.duckdb'
AS fitbit (READ_ONLY);
SHOW TABLES FROM fitbit;
SELECT count(*) FROM fitbit.main.steps;
```

`Catalog "home" does not exist` means the editor parsed `/home/...` as an SQL
identifier instead of a quoted filename.

The dashboard does not attach this file. Its Rust engine opens the Parquet
snapshots with an in-memory DuckDB connection, so normal dashboard reads do not
hold a lock on the ingestion database.

## Design documents

- [Architecture](docs/architecture.md)
- [Data pipeline](docs/data-pipeline.md)
- [Metrics](docs/metrics.md)
- [Local operations](docs/operations.md)
