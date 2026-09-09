# syntax=docker/dockerfile:1

FROM rust:1.98.0-bookworm AS rust-builder

WORKDIR /build
COPY rust/metrics-engine/ ./
RUN --mount=type=cache,id=fitbit-cargo-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=fitbit-cargo-target,target=/build/target \
    cargo build --release --locked \
    && cp target/release/fitbit-metrics /build/fitbit-metrics


FROM rust-builder AS rust-test

RUN --mount=type=cache,id=fitbit-cargo-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=fitbit-cargo-target,target=/build/target \
    cargo test --release --locked


FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/generated \
    FITBIT_DATABASE_FILE=/app/db/fitbit.duckdb \
    FITBIT_PARQUET_DIR=/app/db/parquet \
    FITBIT_METRICS_BINARY=/usr/local/bin/fitbit-metrics \
    HOME=/tmp/fitbit-home

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates libstdc++6 tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin fitbit

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir .

COPY generated/ ./generated/
COPY --from=rust-builder /build/fitbit-metrics /usr/local/bin/fitbit-metrics
RUN mkdir -p /app/db /app/.secrets "$HOME" \
    && chown -R fitbit:fitbit /app/db /app/.secrets "$HOME" \
    && chmod 1777 "$HOME"

USER fitbit
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/_stcore/health', timeout=3)"]

CMD ["streamlit", "run", "src/fitbit/dashboard.py", "--server.address=0.0.0.0", "--server.port=8080", "--server.headless=true"]
