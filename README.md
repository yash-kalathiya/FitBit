# Fitbit local analytics

A lightweight, local-first analytics project built on authorized Google Health
API data from a Fitbit device. The project is being developed manually in small,
tab-focused steps: Python ingestion first, then compact local storage, a Rust
metrics engine, a Streamlit dashboard, and finally local container scheduling.

## Repository layout

```text
src/fitbit/       Hand-written Python application code
generated/        Generated Google Health protobuf/gRPC modules
docs/             Architecture and data-pipeline design documents
.secrets/         Local OAuth configuration and tokens (ignored by Git)
AGENTS.md         Project-specific collaboration and safety guidance
```

## Current smoke test

The current command authenticates, requests one page of sleep data, and prints
the protobuf response. The generated modules are currently a separate Python
import root:

```bash
PYTHONPATH="$PWD/generated" uv run fitbit
```

This command uses private Google Health data. Run it only when an explicit live
API check is intended.

## Design documents

- [Architecture](docs/architecture.md)
- [Data pipeline](docs/data-pipeline.md)
