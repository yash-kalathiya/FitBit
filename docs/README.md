# Design documentation

This directory is the durable home for the project's architecture, design
decisions, data contracts, and operational procedures.

## Current documents

- [Architecture](architecture.md): system boundaries, component flow, and
  delivery phases.
- [Data pipeline](data-pipeline.md): pagination, normalization, storage, and
  incremental ingestion rules.

## Documentation conventions

- Describe the problem and constraints before proposing an implementation.
- Record important tradeoffs and unresolved questions explicitly.
- Update a design document when its corresponding architecture decision
  changes.
- Keep secret values, raw health records, and access tokens out of documentation.
