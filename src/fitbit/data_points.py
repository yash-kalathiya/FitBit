"""Normalize supported Google Health protobuf data points for DuckDB."""

from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any

from google.protobuf.message import Message
from google.devicesandservices.health.v4 import data_points_pb2

from fitbit.data_types import get_data_type_spec


def _timestamp(value: Message) -> datetime:
    return value.ToDatetime(tzinfo=UTC).replace(tzinfo=None)


def _duration_seconds(value: Message) -> float:
    return value.seconds + value.nanos / 1_000_000_000


def _date(value: Message) -> date:
    return date(value.year, value.month, value.day)


def _optional_scalar(message: Message, field_name: str) -> Any | None:
    return getattr(message, field_name) if message.HasField(field_name) else None


def _enum_name(message: Message, field_name: str) -> str:
    field = message.DESCRIPTOR.fields_by_name[field_name]
    return field.enum_type.values_by_number[getattr(message, field_name)].name


def _optional_timestamp(message: Message, field_name: str) -> datetime | None:
    if not message.HasField(field_name):
        return None
    return _timestamp(getattr(message, field_name))


def _timestamp_identity(value: Message) -> bytes:
    return f"{value.seconds}:{value.nanos}".encode()


def _storage_key(
    data_type: str,
    data_point: data_points_pb2.DataPoint,
) -> str:
    """Build a repeatable identity without assuming Google supplies a name."""
    if data_point.name:
        return f"google:{data_point.name}"

    value = getattr(data_point, data_type)
    identity = sha256()
    identity.update(data_type.encode())
    if data_point.HasField("data_source"):
        identity.update(data_point.data_source.SerializeToString(deterministic=True))

    if "interval" in value.DESCRIPTOR.fields_by_name and value.HasField("interval"):
        identity.update(_timestamp_identity(value.interval.start_time))
        identity.update(_timestamp_identity(value.interval.end_time))
    elif (
        "sample_time" in value.DESCRIPTOR.fields_by_name
        and value.HasField("sample_time")
    ):
        identity.update(_timestamp_identity(value.sample_time.physical_time))
    elif "date" in value.DESCRIPTOR.fields_by_name and value.HasField("date"):
        identity.update(
            f"{value.date.year:04}-{value.date.month:02}-{value.date.day:02}".encode()
        )
    else:
        identity.update(data_point.SerializeToString(deterministic=True))

    if data_type == "sleep" and value.HasField("metadata"):
        identity.update(value.metadata.external_id.encode())
    if data_type == "exercise":
        identity.update(str(value.exercise_type).encode())
    return f"local:{data_type}:{identity.hexdigest()}"


def _common(
    data_type: str,
    data_point: data_points_pb2.DataPoint,
) -> dict[str, object]:

    row: dict[str, object] = {
        "storage_key": _storage_key(data_type, data_point),
        "data_point_name": data_point.name or None,
        "recording_method": None,
        "source_platform": None,
        "source_device_form_factor": None,
        "source_device_manufacturer": None,
        "source_device_display_name": None,
        "source_application_package": None,
        "raw_protobuf": data_point.SerializeToString(),
    }
    if not data_point.HasField("data_source"):
        return row

    source = data_point.data_source
    row["recording_method"] = _enum_name(source, "recording_method")
    row["source_platform"] = _enum_name(source, "platform")
    if source.HasField("device"):
        row["source_device_form_factor"] = _enum_name(
            source.device, "form_factor"
        )
        row["source_device_manufacturer"] = source.device.manufacturer or None
        row["source_device_display_name"] = source.device.display_name or None
    if source.HasField("application"):
        row["source_application_package"] = (
            source.application.package_name or None
        )
    return row


def _interval(row: dict[str, object], interval: Message) -> None:
    row.update(
        start_time=_timestamp(interval.start_time),
        start_utc_offset_seconds=(
            _duration_seconds(interval.start_utc_offset)
            if interval.HasField("start_utc_offset")
            else None
        ),
        end_time=_timestamp(interval.end_time),
        end_utc_offset_seconds=(
            _duration_seconds(interval.end_utc_offset)
            if interval.HasField("end_utc_offset")
            else None
        ),
    )


def _steps(data_point: data_points_pb2.DataPoint) -> dict[str, object]:
    value = data_point.steps
    row = _common("steps", data_point)
    _interval(row, value.interval)
    row["count"] = _optional_scalar(value, "count")
    return row


def _heart_rate(data_point: data_points_pb2.DataPoint) -> dict[str, object]:
    value = data_point.heart_rate
    row = _common("heart_rate", data_point)
    row.update(
        sample_time=_timestamp(value.sample_time.physical_time),
        utc_offset_seconds=(
            _duration_seconds(value.sample_time.utc_offset)
            if value.sample_time.HasField("utc_offset")
            else None
        ),
        beats_per_minute=_optional_scalar(value, "beats_per_minute"),
        motion_context=None,
        sensor_location=None,
    )
    if value.HasField("metadata"):
        row["motion_context"] = _enum_name(value.metadata, "motion_context")
        row["sensor_location"] = _enum_name(value.metadata, "sensor_location")
    return row


def _daily_resting_heart_rate(
    data_point: data_points_pb2.DataPoint,
) -> dict[str, object]:
    value = data_point.daily_resting_heart_rate
    row = _common("daily_resting_heart_rate", data_point)
    row.update(
        observation_date=_date(value.date),
        beats_per_minute=_optional_scalar(value, "beats_per_minute"),
        calculation_method=None,
    )
    if value.HasField("daily_resting_heart_rate_metadata"):
        row["calculation_method"] = _enum_name(
            value.daily_resting_heart_rate_metadata,
            "calculation_method",
        )
    return row


def _daily_heart_rate_variability(
    data_point: data_points_pb2.DataPoint,
) -> dict[str, object]:
    value = data_point.daily_heart_rate_variability
    row = _common("daily_heart_rate_variability", data_point)
    row.update(
        observation_date=_date(value.date),
        average_hrv_milliseconds=_optional_scalar(
            value,
            "average_heart_rate_variability_milliseconds",
        ),
        non_rem_heart_rate_bpm=_optional_scalar(
            value,
            "non_rem_heart_rate_beats_per_minute",
        ),
        entropy=_optional_scalar(value, "entropy"),
        deep_sleep_rmssd_milliseconds=_optional_scalar(
            value,
            "deep_sleep_root_mean_square_of_successive_differences_milliseconds",
        ),
    )
    return row


def _daily_vo2_max(data_point: data_points_pb2.DataPoint) -> dict[str, object]:
    value = data_point.daily_vo2_max
    row = _common("daily_vo2_max", data_point)
    row.update(
        observation_date=_date(value.date),
        vo2_max=_optional_scalar(value, "vo2_max"),
        estimated=value.estimated,
        cardio_fitness_level=_enum_name(value, "cardio_fitness_level"),
        vo2_max_covariance=_optional_scalar(value, "vo2_max_covariance"),
    )
    return row


def _sleep(data_point: data_points_pb2.DataPoint) -> dict[str, object]:
    value = data_point.sleep
    row = _common("sleep", data_point)
    _interval(row, value.interval)
    row.update(
        sleep_type=_enum_name(value, "type"),
        minutes_in_sleep_period=None,
        minutes_after_wake_up=None,
        minutes_to_fall_asleep=None,
        minutes_asleep=None,
        minutes_awake=None,
        stages_status=None,
        processed=None,
        is_nap=None,
        manually_edited=None,
        external_id=None,
        is_main_sleep=None,
        create_time=_optional_timestamp(value, "create_time"),
        update_time=_optional_timestamp(value, "update_time"),
    )
    if value.HasField("summary"):
        for field_name in (
            "minutes_in_sleep_period",
            "minutes_after_wake_up",
            "minutes_to_fall_asleep",
            "minutes_asleep",
            "minutes_awake",
        ):
            row[field_name] = _optional_scalar(value.summary, field_name)
    if value.HasField("metadata"):
        row.update(
            stages_status=_enum_name(value.metadata, "stages_status"),
            processed=value.metadata.processed,
            is_nap=value.metadata.nap,
            manually_edited=value.metadata.manually_edited,
            external_id=value.metadata.external_id or None,
            is_main_sleep=value.metadata.main_sleep,
        )
    return row


def _exercise(data_point: data_points_pb2.DataPoint) -> dict[str, object]:
    value = data_point.exercise
    row = _common("exercise", data_point)
    _interval(row, value.interval)
    row.update(
        exercise_type=_enum_name(value, "exercise_type"),
        display_name=value.display_name or None,
        active_duration_seconds=(
            _duration_seconds(value.active_duration)
            if value.HasField("active_duration")
            else None
        ),
        notes=value.notes or None,
        calories_kcal=None,
        distance_millimeters=None,
        steps=None,
        average_speed_millimeters_per_second=None,
        average_pace_seconds_per_meter=None,
        average_heart_rate_beats_per_minute=None,
        elevation_gain_millimeters=None,
        active_zone_minutes=None,
        run_vo2_max=None,
        total_swim_lengths=None,
        pool_length_millimeters=None,
        has_gps=None,
        create_time=_optional_timestamp(value, "create_time"),
        update_time=_optional_timestamp(value, "update_time"),
    )
    if value.HasField("metrics_summary"):
        for field_name in (
            "calories_kcal",
            "distance_millimeters",
            "steps",
            "average_speed_millimeters_per_second",
            "average_pace_seconds_per_meter",
            "average_heart_rate_beats_per_minute",
            "elevation_gain_millimeters",
            "active_zone_minutes",
            "run_vo2_max",
            "total_swim_lengths",
        ):
            row[field_name] = _optional_scalar(value.metrics_summary, field_name)
    if value.HasField("exercise_metadata"):
        row["pool_length_millimeters"] = _optional_scalar(
            value.exercise_metadata, "pool_length_millimeters"
        )
        row["has_gps"] = value.exercise_metadata.has_gps
    return row


_NORMALIZERS = {
    "steps": _steps,
    "heart_rate": _heart_rate,
    "daily_resting_heart_rate": _daily_resting_heart_rate,
    "daily_heart_rate_variability": _daily_heart_rate_variability,
    "daily_vo2_max": _daily_vo2_max,
    "sleep": _sleep,
    "exercise": _exercise,
}


def normalize_data_point(
    data_type: str,
    data_point: data_points_pb2.DataPoint,
) -> dict[str, object]:
    """Convert a supported data point into its complete table row."""
    get_data_type_spec(data_type)
    actual_type = data_point.WhichOneof("data")
    if actual_type != data_type:
        raise ValueError(
            f"expected {data_type!r} data point, received {actual_type!r}"
        )
    return _NORMALIZERS[data_type](data_point)
