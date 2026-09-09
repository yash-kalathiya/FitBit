# Data pipeline

## Goals

- Keep API requests and memory usage bounded.
- Preserve enough provenance to explain every derived metric.
- Avoid large raw JSON payloads.
- Make repeated daily runs safe and idempotent.

## Retrieval

Each request identifies one Google Health data type and a bounded time range.
The client processes one response page, extracts normalized rows, persists the
rows, and then uses `next_page_token` to request the following page. The token
is opaque and is never parsed or modified.

All request fields other than `page_token` remain stable while advancing through
a result set. Daily runs include a small overlap window because wearable records
can sync late or be corrected.

## Normalization

Schemas contain only metric inputs and useful provenance: coordinates, source,
recording method, ingestion time, and the original protobuf bytes. Data-type
fields remain in explicit tables rather than a sparse JSON column.

## Storage

DuckDB is the current system of record. It keeps health tables, ingestion runs,
checkpoints, and original protobuf bytes in one local file. It is the only
authoritative data store.

`db/parquet/` is a derived analytics store with one file per registered type:
`steps.parquet`, `heart_rate.parquet`, `sleep.parquet`, `exercise.parquet`, and
the three daily RHR, HRV, and VO2 files. These files include typed fields and
provenance but omit `raw_protobuf` to reduce size.

The exporter writes every requested type to a unique temporary file from one
consistent DuckDB read transaction. Only after every write succeeds are the
destination files atomically replaced. Ingestion invokes this once after all
requested windows succeed, so a failed update preserves the prior analytics
snapshot. `fitbit export-parquet` rebuilds the layer locally without an API
request.

## Identity and idempotency

Google names are used when present. Because Google documents that most data
points have an empty `name`, anonymous records receive a deterministic key from
their type, source, and time coordinate. Pages are upserted and a checkpoint
advances only after a whole bounded window completes.

## Dashboard contract

The Rust engine owns both queries and formulas. It registers the Parquet files
as views in an in-memory DuckDB connection and returns one compact JSON report
cached by Streamlit for five minutes. Streamlit does not load raw history,
attach the ingestion database, or recalculate metrics in Python.
