"""Streamlit presentation for compact results from the Rust metrics engine."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from fitbit.metrics_client import load_metrics


st.set_page_config(
    page_title="Fitbit Field Notes",
    page_icon="◉",
    layout="wide",
)
st.markdown(
    """
    <style>
    .stApp { background: #07130f; color: #eaf7ef; }
    [data-testid="stMetric"] {
      background: linear-gradient(145deg, #11261e, #0b1b15);
      border: 1px solid #234f3d; border-radius: 16px; padding: 18px;
    }
    [data-testid="stSidebar"] { background: #0a1a14; }
    h1, h2, h3 { letter-spacing: -0.03em; }
    .eyebrow { color: #6ee7a8; font-weight: 700; letter-spacing: .12em; }
    .muted { color: #9fb9ab; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _value(value: object, suffix: str = "", digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "Not enough data"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _minutes_to_clock(value: float | None) -> str | None:
    if value is None:
        return None
    minutes = int(round(value)) % 1_440
    return f"{minutes // 60:02}:{minutes % 60:02}"


with st.sidebar:
    st.header("Personal reference")
    age = st.number_input("Chronological age", min_value=18, max_value=100, value=30)
    reference_label = st.radio(
        "VO₂ population reference",
        ("Male reference", "Female reference"),
        help="Used only for the transparent fitness-age estimate.",
    )
    reference_sex = reference_label.split()[0].lower()
    st.caption(
        "These choices stay in this browser session and are not written to the database."
    )
    if st.button("Refresh local analysis", width="stretch"):
        st.cache_data.clear()


@st.cache_data(ttl=300, show_spinner="Running local Rust analytics…")
def _cached_metrics(age_value: int, sex_value: str) -> dict:
    return load_metrics(age=age_value, reference_sex=sex_value)


st.markdown('<div class="eyebrow">LOCAL HEALTH ANALYTICS</div>', unsafe_allow_html=True)
st.title("Fitbit Field Notes")
st.caption("Patterns your standard dashboard does not put together in one place.")

try:
    report = _cached_metrics(int(age), reference_sex)
except RuntimeError as error:
    st.error(str(error))
    st.info("Run the initial backfill and make sure the Rust metrics binary is available.")
    st.stop()

summary = report["summary"]
fitness_age = summary.get("fitness_age")
regularity = summary.get("sleep_regularity")
recovery_lag = summary.get("recovery_lag")
readiness = summary.get("personal_readiness")
sleep_opportunity = summary.get("sleep_opportunity")

cards = st.columns(4)
cards[0].metric("Latest daily strain", _value(summary.get("latest_daily_strain"), "/21"))
cards[1].metric(
    "Fitness age estimate",
    _value(fitness_age.get("estimated_years") if fitness_age else None, " yr"),
    _value(fitness_age.get("difference_years") if fitness_age else None, " yr vs age"),
)
cards[2].metric(
    "Sleep regularity",
    _value(regularity.get("score") if regularity else None, "/100", 0),
    (
        _value(regularity.get("midpoint_variability_minutes"), " min spread", 0)
        if regularity
        else None
    ),
)
cards[3].metric(
    "Personal readiness",
    _value(readiness.get("score") if readiness else None, "/100", 0),
    (
        f"{readiness['mean_similarity_percent']:.0f}% analog similarity"
        if readiness
        else None
    ),
)

daily = pd.DataFrame(report["daily"])
if daily.empty:
    st.warning("The database contains no metric-ready daily records yet.")
    st.stop()
daily["date"] = pd.to_datetime(daily["date"])
daily = daily.set_index("date")

st.subheader("Daily load, without the weekly carry-over")
left, right = st.columns((2, 1))
with left:
    st.line_chart(
        daily[["strain"]].dropna(),
        color=["#6ee7a8"],
        y_label="Daily strain (0–21)",
    )
with right:
    recent = summary.get("recent_load")
    if recent:
        st.metric("7-day / prior baseline", f"{recent['ratio']:.2f}×")
        st.write(recent["interpretation"])
    latest = daily.iloc[-1]
    st.caption(
        f"Observed HR: {_value(latest.get('observed_hours'), ' h')} · "
        f"active: {_value(latest.get('active_minutes'), ' min')} · "
        f"zone 2+: {_value(latest.get('zone_2_plus_minutes'), ' min')}"
    )

st.subheader("Sleep response and timing")
sleep_left, sleep_right = st.columns((3, 2))
with sleep_left:
    st.line_chart(
        daily[["sleep_hours"]].dropna(),
        color=["#8fb8ff"],
        y_label="Sleep hours",
    )
with sleep_right:
    response = summary.get("strain_sleep_response")
    if response:
        st.metric(
            "Sleep after high-strain days",
            _value(response["high_vs_typical_sleep_delta_hours"], " h"),
        )
        st.write(response["interpretation"])
        st.caption(
            f"{response['paired_days']} paired days · correlation "
            f"{response['correlation']:.2f}"
        )
    if regularity:
        latest_midpoint = next(
            (
                _minutes_to_clock(value)
                for value in reversed(daily["sleep_midpoint_minute"].tolist())
                if pd.notna(value)
            ),
            None,
        )
        st.caption(f"Latest sleep midpoint: {latest_midpoint or 'not available'}")

st.subheader("What similar mornings suggest")
readiness_left, readiness_right = st.columns((3, 2))
with readiness_left:
    if readiness:
        st.metric(
            "Expected strain capacity",
            f"{readiness['expected_strain_capacity']:.1f}/21",
            help="Similarity-weighted strain completed on the five closest historical mornings.",
        )
        st.write(readiness["interpretation"])
        analog_frame = pd.DataFrame(readiness["analog_days"])
        analog_frame["date"] = pd.to_datetime(analog_frame["date"]).dt.date
        st.dataframe(
            analog_frame.rename(
                columns={
                    "date": "Analog date",
                    "similarity_percent": "Similarity %",
                    "completed_strain": "Completed strain",
                }
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("At least 15 complete morning profiles are needed for analog matching.")
with readiness_right:
    if sleep_opportunity:
        st.metric(
            "Tonight's sleep opportunity",
            f"{sleep_opportunity['estimated_hours']:.1f} h",
            f"target {sleep_opportunity['target_recovery_score']:.0f}/100 physiology",
        )
        st.write(sleep_opportunity["interpretation"])
        st.caption(
            f"{sleep_opportunity['training_days']} training days · model R² "
            f"{sleep_opportunity['model_r_squared']:.2f} · bounded to "
            f"{sleep_opportunity['observed_lower_bound_hours']:.1f}–"
            f"{sleep_opportunity['observed_upper_bound_hours']:.1f} h"
        )
    else:
        st.info(
            "The sleep model needs 30 paired days and a positive sleep/recovery "
            "relationship in your own data."
        )
    if recovery_lag:
        st.metric(
            "Typical recovery lag after hard days",
            f"{recovery_lag['median_days_to_baseline']:.1f} days",
        )

st.subheader("Signals behind the estimate")
signal_left, signal_right = st.columns((3, 2))
with signal_left:
    morning = daily[
        ["sleep_hours", "resting_heart_rate", "hrv_milliseconds"]
    ].copy()
    baseline = morning.rolling(28, min_periods=7).median().shift(1)
    relative = pd.DataFrame(
        {
            "Sleep": (morning["sleep_hours"] / baseline["sleep_hours"] - 1.0)
            * 100.0,
            "HRV": (
                morning["hrv_milliseconds"]
                / baseline["hrv_milliseconds"]
                - 1.0
            )
            * 100.0,
            "RHR": (
                baseline["resting_heart_rate"]
                / morning["resting_heart_rate"]
                - 1.0
            )
            * 100.0,
        },
        index=daily.index,
    ).replace([float("inf"), float("-inf")], pd.NA)
    st.line_chart(
        relative.dropna(how="all"),
        y_label="Favorable change vs prior 28-day median (%)",
    )
    st.caption(
        "All three lines use one comparable scale; positive means more sleep, "
        "higher HRV, or lower RHR than the preceding baseline."
    )
with signal_right:
    st.line_chart(
        daily[["steps"]].dropna(),
        color=["#f4c96b"],
        y_label="Daily steps",
    )
    st.caption("Steps are separate so their larger units do not flatten physiology.")

with st.expander("Coverage and methodology"):
    coverage = report["coverage"]
    st.write(
        f"Coverage: {coverage.get('first_date') or 'unknown'} to "
        f"{coverage.get('last_date') or 'unknown'}; "
        f"{coverage['heart_rate_samples']:,} heart-rate samples, "
        f"{coverage['sleep_days']} sleep days, {coverage['vo2_days']} VO₂ days."
    )
    for note in report["methodology"]:
        st.markdown(f"- {note}")
    st.caption(
        "Generated locally at "
        + datetime.fromisoformat(report["generated_at"]).astimezone().strftime(
            "%Y-%m-%d %H:%M %Z"
        )
    )
