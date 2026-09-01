# Fitbit Project Guidance

## Project goal

Build a lightweight, local-first dashboard over authorized Google Health API
data from a regularly used Fitbit device. The system should ingest bounded
date ranges, store compact local data, compute transparent metrics in Rust, and
present those metrics in Streamlit. It should eventually run locally in a
container on a daily schedule.

## Collaboration style

- Prefer manual, tab-based coding. Make one focused change at a time and explain
  the responsibility of the active file.
- Unless the user explicitly asks for implementation, provide guidance, hints,
  review, or diagnosis without editing files.
- Keep changes small enough for the user to understand and verify manually.
- Diagnose from concrete errors and current behavior before proposing broad
  refactors.
- Do not fabricate Google Health API capabilities, metric meanings, or health
  conclusions. Treat correlations as non-medical observations.

## Safety boundaries

- Never read, print, commit, or copy values from `.secrets/`.
- Never edit files under `generated/`; regenerate them from pinned protobuf
  inputs instead.
- Keep OAuth tokens, client configuration, local health data, and derived user
  data out of Git and container images.
- Use read-only Google Health scopes unless a feature explicitly requires a
  write scope.

## Code boundaries

- `src/fitbit/auth.py`: acquire, load, and refresh OAuth credentials only.
- `src/fitbit/health_client.py`: construct authenticated gRPC clients and make
  Google Health API calls.
- `src/fitbit/main.py`: compose application components and expose the CLI entry
  point.
- `generated/`: generated protobuf and gRPC code.
- `docs/`: design documents, decisions, and operational notes.
- Future ingestion, storage, metrics, and dashboard modules should remain
  separate rather than accumulating in one script.

## Data and performance principles

- Fetch bounded date windows and process one page at a time.
- Treat `next_page_token` as an opaque pagination cursor.
- Avoid retaining full JSON responses by default. Normalize only required
  fields and prefer compressed Parquet for analytical history.
- Deduplicate by stable data-point identity and allow a small overlap window for
  delayed Fitbit synchronization.
- Keep Streamlit presentation separate from ingestion and metric computation.

## Verification

- Run focused import or unit checks before a live API call.
- Keep live OAuth/API checks explicit because they use private data and network
  access.
- Verify that `.secrets/`, local data, caches, and tokens remain ignored before
  staging changes.
