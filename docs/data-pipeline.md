# Data pipeline

## Goals

- Keep API requests and memory usage bounded.
- Preserve enough provenance to explain every derived metric.
- Avoid storing large raw JSON payloads unless they are needed temporarily for
  diagnosis.
- Make repeated daily runs safe and idempotent.

## Retrieval

Each request identifies one Google Health data type and a bounded time range.
The client processes one response page, extracts normalized rows, persists the
rows, and then uses `next_page_token` to request the following page. The token
is opaque and is never parsed or modified.

All request fields other than `page_token` remain stable while advancing through
a result set. Daily runs should include a small overlap window because wearable
records can sync late or be corrected.

## Normalization

The normalized schema should contain only fields needed by defined metrics.
Shared provenance fields are expected to include:

- stable Google Health data-point identity when available;
- data type;
- observation start/end or sample time;
- source platform and recording method when useful;
- ingestion timestamp;
- values required for the metric family.

Data-type-specific fields belong in explicit schemas rather than one sparse,
unbounded JSON column.

## Storage

Compressed Parquet is the preferred analytical history format because it is
columnar, compact, and accessible from both Python and Rust. Partitioning should
start simply, likely by data type and month, and become more detailed only when
measured query behavior requires it.

A small local state store can track successful windows and deduplication state.
Raw protobuf-to-JSON output is optional and should have a short, explicit
retention policy because it is both large and sensitive.

## Idempotency

Daily ingestion must tolerate rerunning the same date range. Normalize stable
identities, deduplicate before publication, and write completed pages or windows
atomically so a failed run does not appear complete.

## Dashboard contract

Streamlit consumes small metric results or daily summaries. It does not load all
raw historical records or recalculate the entire dataset on every page refresh.
