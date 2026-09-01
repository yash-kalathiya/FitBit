"""Local configuration for the Fitbit analytics application."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRETS_DIR = Path(os.environ.get("FITBIT_SECRETS_DIR", PROJECT_ROOT / ".secrets"))

CLIENT_SECRETS_FILE = Path(
    os.environ.get(
        "FITBIT_CLIENT_SECRETS_FILE",
        SECRETS_DIR / "client_secrets_desktop.json",
    )
)
TOKEN_FILE = Path(
    os.environ.get(
        "FITBIT_TOKEN_FILE",
        SECRETS_DIR / "token.json",
    )
)

GRPC_TARGET = "health.googleapis.com:443"

SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    "https://www.googleapis.com/auth/googlehealth.profile.readonly",
    "https://www.googleapis.com/auth/googlehealth.settings.readonly",
    "https://www.googleapis.com/auth/googlehealth.location.readonly",
    "https://www.googleapis.com/auth/googlehealth.ecg.readonly",
    "https://www.googleapis.com/auth/googlehealth.irn.readonly",
)
