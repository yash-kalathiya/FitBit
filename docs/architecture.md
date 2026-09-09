# Architecture

## Purpose

Create a lightweight, local-first Fitbit analytics system using authorized
Google Health API data. The dashboard emphasizes transparent, user-defined
analysis that combines signals the standard Fitbit app does not put together.

## System flow

```text
Google Health API (gRPC)
    -> Python authentication and bounded ingestion
    -> one private DuckDB system of record
    -> atomic per-type Parquet analytics snapshots
    -> read-only Rust query and metric executable over in-memory DuckDB
    -> small JSON response
    -> Streamlit dashboard
    -> dashboard container + twice-daily ingestion sidecar
```

## Component boundaries

### Python authentication

Owns OAuth credential loading, refresh, and initial browser authorization. It
does not fetch health records or calculate metrics.

### Python health client and ingestion

Constructs authenticated gRPC clients, applies date filters, follows pagination,
and converts protobuf records into typed rows. It processes one page at a time
so response history does not accumulate in memory.

### Local storage

Stores normalized, typed fields plus original protobuf bytes in one private
DuckDB file. The same database contains schema version, ingestion-run
information, and per-type checkpoints. DuckDB is the only authoritative copy.

After a complete successful ingestion, storage writes the selected typed tables
to temporary Zstandard-compressed Parquet files and atomically replaces the
corresponding snapshots. Raw protobuf bytes are intentionally excluded from
this derived layer. A failed ingestion or export leaves the last usable
snapshots in place, and all snapshots can be rebuilt from DuckDB.

### Rust metrics engine

Creates an in-memory DuckDB connection over the per-type Parquet snapshots,
deduplicates analytical inputs, and calculates daily strain, fitness age,
analog-day readiness, sleep opportunity, recovery lag, and sleep regularity. A
command-line executable emits a bounded JSON document for Streamlit; there is
no extra network service or duplicate Python formula layer. A direct
`--database` input remains available for diagnosis, but is not the dashboard
default.

### Streamlit dashboard

Presents metrics and user-defined comparisons. It does not authenticate with
Google, process protobuf records, or implement core metric formulas.

### Local operations

Compose runs the same image in two roles. `dashboard` binds only to
`127.0.0.1:8080`; `ingestion` runs at 08:00 and 20:00 local time. Data is mounted
into both services and secrets only into ingestion. Only the ingestion service
opens the persistent DuckDB database during normal operation; the dashboard
reads atomically replaced Parquet files and does not compete for its writer
lock.

## Delivery and observation phases

1. Authorize once and complete the initial 120-day backfill.
2. Validate the Rust report against populated local tables.
3. Run the Streamlit dashboard and twice-daily sidecar with Compose.
4. Observe at least 30 complete paired days before interpreting regression.
5. Add new source types only when a defined metric requires them.

## Open decisions

- Whether raw protobuf retention should become time-bounded after schemas settle.
- Whether a richer model materially improves held-out prediction over the small
  regularized regression.
