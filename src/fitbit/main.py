"""Command-line entry point for the current Google Health API smoke test."""

from __future__ import annotations

import grpc

from fitbit.auth import get_credentials
from fitbit.health_client import list_sleep_data_points


def main() -> None:
    """Authenticate, fetch one page of sleep data, and print the response."""
    try:
        response = list_sleep_data_points(get_credentials())
    except grpc.RpcError as error:
        print(f"Google Health gRPC error: {error.code()} - {error.details()}")
        return

    print("Health data via gRPC:", response)


if __name__ == "__main__":
    main()
