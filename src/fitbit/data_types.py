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
    filter_field: str
    page_size: int
    columns: tuple[ColumnSpec, ...]
    filter_uses_civil_time: bool = False
    filter_uses_date: bool = False
    table_constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.filter_uses_civil_time and self.filter_uses_date:
            raise ValueError("a data type cannot use both civil-time and date filters")

    @property
    def resource_name(self) -> str:
        """Return the kebab-case identifier used in Google resource paths."""
        return self.api_name.replace("_", "-")

    def create_table_sql(self, table_name: str | None = None) -> str:
        """Build the idempotent CREATE TABLE statement for this schema."""
        definitions = [column.sql_definition() for column in self.columns]
        definitions.extend(self.table_constraints)
        body = ",\n    ".join(definitions)
        name = table_name or self.table_name
        return f'CREATE TABLE IF NOT EXISTS "{name}" (\n    {body}\n);'


_COMMON_COLUMNS = (
    ColumnSpec("storage_key", "VARCHAR", nullable=False, primary_key=True),
    ColumnSpec("data_point_name", "VARCHAR"),
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
        filter_field="steps.interval.start_time",
        page_size=1_000,
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
        filter_field="heart_rate.sample_time.physical_time",
        page_size=1_000,
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
        api_name="daily_resting_heart_rate",
        table_name="daily_resting_heart_rate",
        filter_field="daily_resting_heart_rate.date",
        page_size=365,
        filter_uses_date=True,
        columns=(
            _COMMON_COLUMNS
            + (
                ColumnSpec("observation_date", "DATE", nullable=False),
                ColumnSpec("beats_per_minute", "USMALLINT", nullable=False),
                ColumnSpec("calculation_method", "VARCHAR"),
            )
        ),
    ),
    DataTypeSpec(
        api_name="daily_heart_rate_variability",
        table_name="daily_heart_rate_variability",
        filter_field="daily_heart_rate_variability.date",
        page_size=365,
        filter_uses_date=True,
        columns=(
            _COMMON_COLUMNS
            + (
                ColumnSpec("observation_date", "DATE", nullable=False),
                ColumnSpec("average_hrv_milliseconds", "DOUBLE"),
                ColumnSpec("non_rem_heart_rate_bpm", "DOUBLE"),
                ColumnSpec("entropy", "DOUBLE"),
                ColumnSpec("deep_sleep_rmssd_milliseconds", "DOUBLE"),
            )
        ),
    ),
    DataTypeSpec(
        api_name="daily_vo2_max",
        table_name="daily_vo2_max",
        filter_field="daily_vo2_max.date",
        page_size=365,
        filter_uses_date=True,
        columns=(
            _COMMON_COLUMNS
            + (
                ColumnSpec("observation_date", "DATE", nullable=False),
                ColumnSpec("vo2_max", "DOUBLE", nullable=False),
                ColumnSpec("estimated", "BOOLEAN"),
                ColumnSpec("cardio_fitness_level", "VARCHAR"),
                ColumnSpec("vo2_max_covariance", "DOUBLE"),
            )
        ),
    ),
    DataTypeSpec(
        api_name="sleep",
        table_name="sleep",
        filter_field="sleep.interval.end_time",
        page_size=25,
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
        filter_field="exercise.interval.civil_start_time",
        page_size=25,
        filter_uses_civil_time=True,
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
