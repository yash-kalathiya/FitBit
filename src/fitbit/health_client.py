"""Authenticated, bounded Google Health API gRPC pagination."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from google.auth.credentials import Credentials
from google.auth.transport.grpc import secure_authorized_channel
from google.auth.transport.requests import Request
from google.devicesandservices.health.v4 import data_points_pb2
from google.devicesandservices.health.v4 import data_points_pb2_grpc

from fitbit.config import GRPC_TARGET
from fitbit.data_types import get_data_type_spec


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Google Health filter datetimes must be timezone-aware")
    return value


def _rfc3339(value: datetime) -> str:
    return (
        _require_aware(value)
        .astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def build_time_filter(
    data_type: str,
    window_start: datetime,
    window_end: datetime,
) -> str:
    """Build the supported half-open Google Health time filter."""
    _require_aware(window_start)
    _require_aware(window_end)
    if window_start >= window_end:
        raise ValueError("window_start must be earlier than window_end")

    spec = get_data_type_spec(data_type)
    if spec.filter_uses_date:
        start_literal = _require_aware(window_start).astimezone(UTC).date().isoformat()
        end_literal = _require_aware(window_end).astimezone(UTC).date().isoformat()
    elif spec.filter_uses_civil_time:
        start_literal = _require_aware(window_start).astimezone(UTC).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        end_literal = _require_aware(window_end).astimezone(UTC).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    else:
        start_literal = _rfc3339(window_start)
        end_literal = _rfc3339(window_end)

    field = spec.filter_field
    return f'{field} >= "{start_literal}" AND {field} < "{end_literal}"'


def _iter_pages_from_stub(
    stub: data_points_pb2_grpc.DataPointsServiceStub,
    data_type: str,
    window_start: datetime,
    window_end: datetime,
) -> Iterator[tuple[data_points_pb2.DataPoint, ...]]:
    spec = get_data_type_spec(data_type)
    page_token = ""
    seen_tokens: set[str] = set()

    while True:
        request = data_points_pb2.ListDataPointsRequest(
            parent=f"users/me/dataTypes/{spec.resource_name}",
            page_size=spec.page_size,
            page_token=page_token,
            filter=build_time_filter(data_type, window_start, window_end),
        )
        response = stub.ListDataPoints(request, timeout=60)
        yield tuple(response.data_points)

        next_token = response.next_page_token
        if not next_token:
            return
        if next_token in seen_tokens:
            raise RuntimeError("Google Health returned a repeated page token")
        seen_tokens.add(next_token)
        page_token = next_token


def iter_data_point_pages(
    credentials: Credentials,
    data_type: str,
    window_start: datetime,
    window_end: datetime,
) -> Iterator[tuple[data_points_pb2.DataPoint, ...]]:
    """Yield one API response page at a time and close the channel afterward."""
    channel = secure_authorized_channel(credentials, Request(), GRPC_TARGET)
    try:
        stub = data_points_pb2_grpc.DataPointsServiceStub(channel)
        yield from _iter_pages_from_stub(
            stub,
            data_type,
            window_start,
            window_end,
        )
    finally:
        channel.close()
