"""Typed boundary between Streamlit and the Rust metrics executable."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from fitbit.config import PARQUET_DIR


METRICS_BINARY = os.environ.get("FITBIT_METRICS_BINARY", "fitbit-metrics")


def load_metrics(
    *,
    age: int,
    reference_sex: str,
    parquet_dir: Path = PARQUET_DIR,
    attempts: int = 3,
) -> dict[str, Any]:
    """Execute the Rust engine against immutable Parquet snapshots."""
    if not 18 <= age <= 100:
        raise ValueError("age must be between 18 and 100")
    if reference_sex not in {"male", "female"}:
        raise ValueError("reference_sex must be male or female")
    if attempts <= 0:
        raise ValueError("attempts must be positive")

    command = [
        METRICS_BINARY,
        "--parquet-directory",
        str(parquet_dir),
        "--age",
        str(age),
        "--reference-sex",
        reference_sex,
    ]
    last_error = "metrics engine did not run"
    for attempt in range(attempts):
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=90,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                f"Rust metrics executable was not found: {METRICS_BINARY}"
            ) from error
        except subprocess.TimeoutExpired as error:
            last_error = "Rust metrics engine timed out"
        else:
            if result.returncode == 0:
                try:
                    payload = json.loads(result.stdout)
                except json.JSONDecodeError as error:
                    raise RuntimeError(
                        "Rust metrics engine returned invalid JSON"
                    ) from error
                if not isinstance(payload, dict) or "summary" not in payload:
                    raise RuntimeError("Rust metrics response is missing summary")
                return payload
            last_error = result.stderr.strip() or "Rust metrics engine failed"

        if attempt + 1 < attempts:
            time.sleep(0.75 * (attempt + 1))
    raise RuntimeError(last_error)
