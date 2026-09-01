# Architecture

## Purpose

Create a lightweight, local-first Fitbit analytics system using authorized
Google Health API data. The dashboard should emphasize transparent,
user-defined analysis that is meaningfully different from the standard Fitbit
or Google Health app.

## System flow

```text
Google Health API (gRPC)
    -> Python authentication and bounded ingestion
    -> compact local analytical storage
    -> Rust query and metric engine
    -> Python binding
    -> Streamlit dashboard
    -> local container and daily scheduler
```

## Component boundaries

### Python authentication

Owns OAuth credential loading, refresh, and initial browser authorization. It
does not fetch health records or calculate metrics.

### Python health client and ingestion

Constructs authenticated gRPC clients, applies date filters, follows pagination,
and converts protobuf records into an internal data contract. It processes one
page at a time so response history does not accumulate in memory.

### Local storage

Stores only the fields needed for planned analysis. Compressed Parquet is the
preferred analytical format. A small state store may track ingestion windows,
checkpoints, and stable data-point identities. Raw JSON is optional diagnostic
data with bounded retention, not the primary store.

### Rust metrics engine

Reads normalized local data and calculates transparent metrics. It should begin
as a small library rather than a separate network service. A Python binding can
expose dashboard-oriented functions to Streamlit.

### Streamlit dashboard

Presents metrics and user-defined comparisons. It does not authenticate with
Google, process protobuf records, or implement core metric formulas.

### Local operations

The native workflow must work before containerization. The eventual container
mounts secrets and data at runtime; neither belongs in the image. A host-level
user timer can trigger bounded daily ingestion.

## Initial delivery phases

1. Separate authentication from one successful gRPC data request.
2. Add filtered, paginated ingestion for one data type.
3. Define and write a compact normalized data contract.
4. Add idempotent daily ingestion and local state.
5. Implement one transparent Rust metric and expose it to Python.
6. Build a minimal Streamlit question-and-comparison view.
7. Add container packaging and a daily local schedule.

## Open decisions

- Exact normalized schemas for sleep, activity, and heart metrics.
- Parquet library and partitioning strategy.
- Rust analytical library and Python binding interface.
- Raw diagnostic retention policy.
- First custom analysis that provides value beyond the Google Health app.
