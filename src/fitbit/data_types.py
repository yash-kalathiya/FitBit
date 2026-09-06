"""Supported Google Health data types and their DuckDB table schemas."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """Describe one trusted column in a local DuckDB table."""

    name: str
    sql_type: str
    nullable: bool = True
    primary_key: bool = False
    default_sql: str | None = None

    def sql_definition(self) -> str:
        """Render this trusted column definition as DuckDB SQL."""
        parts = [f'"{self.name}"', self.sql_type]
        if not self.nullable:
            parts.append("NOT NULL")
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if self.default_sql is not None:
            parts.extend(("DEFAULT", self.default_sql))
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class DataTypeSpec:
    """Map one Google Health API data type to its local table schema."""

    api_name: str
    table_name: str
    columns: tuple[ColumnSpec, ...]
    table_constraints: tuple[str, ...] = ()

    def create_table_sql(self) -> str:
        """Build the idempotent CREATE TABLE statement for this schema."""
        definitions = [column.sql_definition() for column in self.columns]
        definitions.extend(self.table_constraints)
        body = ",\n    ".join(definitions)
        return f'CREATE TABLE IF NOT EXISTS "{self.table_name}" (\n    {body}\n);'


_COMMON_COLUMNS = (
    ColumnSpec("data_point_name", "VARCHAR", nullable=False, primary_key=True),
    ColumnSpec("recording_method", "VARCHAR"),
    ColumnSpec("source_platform", "VARCHAR"),
    ColumnSpec("source_device_form_factor", "VARCHAR"),
    ColumnSpec("source_device_manufacturer", "VARCHAR"),
    ColumnSpec("source_device_display_name", "VARCHAR"),
    ColumnSpec("source_application_package", "VARCHAR"),
    ColumnSpec("raw_protobuf", "BLOB", nullable=False),
    ColumnSpec("ingested_at", "TIMESTAMP", nullable=False, default_sql="now()"),
)

_INTERVAL_COLUMNS = (
    ColumnSpec("start_time", "TIMESTAMP", nullable=False),
    ColumnSpec("start_utc_offset_seconds", "BIGINT"),
    ColumnSpec("end_time", "TIMESTAMP", nullable=False),
    ColumnSpec("end_utc_offset_seconds", "BIGINT"),
)

_CREATED_UPDATED_COLUMNS = (
    ColumnSpec("create_time", "TIMESTAMP"),
    ColumnSpec("update_time", "TIMESTAMP"),
)


_SPECS = (
    DataTypeSpec(
        api_name="steps",
        table_name="steps",
        columns=(
            _COMMON_COLUMNS
            + _INTERVAL_COLUMNS
            + (ColumnSpec("count", "UBIGINT", nullable=False),)
        ),
        table_constraints=("CHECK (start_time < end_time)",),
    ),
    DataTypeSpec(
        api_name="heart_rate",
        table_name="heart_rate",
        columns=(
            _COMMON_COLUMNS
            + (
                ColumnSpec("sample_time", "TIMESTAMP", nullable=False),
                ColumnSpec("utc_offset_seconds", "BIGINT"),
                ColumnSpec("beats_per_minute", "USMALLINT", nullable=False),
                ColumnSpec("motion_context", "VARCHAR"),
                ColumnSpec("sensor_location", "VARCHAR"),
            )
        ),
    ),
    DataTypeSpec(
        api_name="sleep",
        table_name="sleep",
        columns=(
            _COMMON_COLUMNS
            + _INTERVAL_COLUMNS
            + (
                ColumnSpec("sleep_type", "VARCHAR", nullable=False),
                ColumnSpec("minutes_in_sleep_period", "BIGINT"),
                ColumnSpec("minutes_after_wake_up", "BIGINT"),
                ColumnSpec("minutes_to_fall_asleep", "BIGINT"),
                ColumnSpec("minutes_asleep", "BIGINT"),
                ColumnSpec("minutes_awake", "BIGINT"),
                ColumnSpec("stages_status", "VARCHAR"),
                ColumnSpec("processed", "BOOLEAN"),
                ColumnSpec("is_nap", "BOOLEAN"),
                ColumnSpec("manually_edited", "BOOLEAN"),
                ColumnSpec("external_id", "VARCHAR"),
                ColumnSpec("is_main_sleep", "BOOLEAN"),
            )
            + _CREATED_UPDATED_COLUMNS
        ),
        table_constraints=("CHECK (start_time < end_time)",),
    ),
    DataTypeSpec(
        api_name="exercise",
        table_name="exercise",
        columns=(
            _COMMON_COLUMNS
            + _INTERVAL_COLUMNS
            + (
                ColumnSpec("exercise_type", "VARCHAR", nullable=False),
                ColumnSpec("display_name", "VARCHAR"),
                ColumnSpec("active_duration_seconds", "DOUBLE"),
                ColumnSpec("notes", "VARCHAR"),
                ColumnSpec("calories_kcal", "DOUBLE"),
                ColumnSpec("distance_millimeters", "DOUBLE"),
                ColumnSpec("steps", "UBIGINT"),
                ColumnSpec("average_speed_millimeters_per_second", "DOUBLE"),
                ColumnSpec("average_pace_seconds_per_meter", "DOUBLE"),
                ColumnSpec("average_heart_rate_beats_per_minute", "UBIGINT"),
                ColumnSpec("elevation_gain_millimeters", "DOUBLE"),
                ColumnSpec("active_zone_minutes", "UBIGINT"),
                ColumnSpec("run_vo2_max", "DOUBLE"),
                ColumnSpec("total_swim_lengths", "DOUBLE"),
                ColumnSpec("pool_length_millimeters", "BIGINT"),
                ColumnSpec("has_gps", "BOOLEAN"),
            )
            + _CREATED_UPDATED_COLUMNS
        ),
        table_constraints=("CHECK (start_time < end_time)",),
    ),
)

DATA_TYPE_REGISTRY: Mapping[str, DataTypeSpec] = MappingProxyType(
    {spec.api_name: spec for spec in _SPECS}
)


def get_data_type_spec(api_name: str) -> DataTypeSpec:
    """Return a supported data type or raise a helpful configuration error."""
    try:
        return DATA_TYPE_REGISTRY[api_name]
    except KeyError as error:
        supported = ", ".join(DATA_TYPE_REGISTRY)
        raise ValueError(
            f"unsupported data type {api_name!r}; supported types: {supported}"
        ) from error
