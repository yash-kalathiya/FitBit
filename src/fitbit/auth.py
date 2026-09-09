"""OAuth credential loading and refresh for Google Health API access."""

from __future__ import annotations

import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from fitbit.config import CLIENT_SECRETS_FILE, SCOPES, TOKEN_FILE


def _save_credentials(credentials: Credentials) -> None:
    """Persist refreshed credentials without logging sensitive values."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")


def get_credentials(*, interactive: bool = True) -> Credentials:
    """Load credentials, optionally allowing an interactive browser flow."""
    credentials = None

    if TOKEN_FILE.exists():
        with TOKEN_FILE.open(encoding="utf-8") as token_file:
            credentials = Credentials.from_authorized_user_info(
                json.load(token_file),
                SCOPES,
            )

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            _save_credentials(credentials)
        except Exception:
            credentials = None

    if not credentials or not credentials.valid:
        if not interactive:
            raise RuntimeError(
                "Google Health credentials require interactive authorization; "
                "run `fitbit backfill --days 1` once on the host"
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            SCOPES,
        )
        credentials = flow.run_local_server(port=0)
        _save_credentials(credentials)

    return credentials
