"""Authenticated Google Health API gRPC calls."""

from __future__ import annotations

from google.auth.credentials import Credentials
from google.auth.transport.grpc import secure_authorized_channel
from google.auth.transport.requests import Request
from google.devicesandservices.health.v4 import data_points_pb2
from google.devicesandservices.health.v4 import data_points_pb2_grpc

from fitbit.config import GRPC_TARGET


def list_sleep_data_points(credentials: Credentials):
    """Fetch one page of sleep data points for the authenticated user."""
    channel = secure_authorized_channel(credentials, Request(), GRPC_TARGET)
    try:
        stub = data_points_pb2_grpc.DataPointsServiceStub(channel)
        request = data_points_pb2.ListDataPointsRequest(
            parent="users/me/dataTypes/sleep"
        )
        return stub.ListDataPoints(request)
    finally:
        channel.close()
