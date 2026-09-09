use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use std::f64::consts::TAU;
use std::fmt;
use std::path::Path;
use std::str::FromStr;

use anyhow::{Context, Result, bail};
use chrono::{Duration, NaiveDate, NaiveDateTime, Timelike, Utc};
use duckdb::{AccessMode, Config, Connection};
use serde::Serialize;

const MAX_SAMPLE_GAP_SECONDS: i64 = 120;
const FITNESS_AGE_MIN: f64 = 18.0;
const FITNESS_AGE_MAX: f64 = 90.0;
const ANALYTICS_TABLES: [&str; 7] = [
    "steps",
    "heart_rate",
    "daily_resting_heart_rate",
    "daily_heart_rate_variability",
    "daily_vo2_max",
    "sleep",
    "exercise",
];

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceSex {
    Male,
    Female,
}

impl fmt::Display for ReferenceSex {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Male => write!(formatter, "male"),
            Self::Female => write!(formatter, "female"),
        }
    }
}

impl FromStr for ReferenceSex {
    type Err = anyhow::Error;

    fn from_str(value: &str) -> Result<Self> {
        match value.to_ascii_lowercase().as_str() {
            "male" => Ok(Self::Male),
            "female" => Ok(Self::Female),
            _ => bail!("reference sex must be male or female"),
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct MetricProfile {
    pub age: u8,
    pub reference_sex: ReferenceSex,
}

#[derive(Debug, Serialize)]
pub struct DashboardReport {
    pub generated_at: String,
    pub profile: ProfileReport,
    pub coverage: Coverage,
    pub summary: MetricSummary,
    pub daily: Vec<DailyMetric>,
    pub methodology: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct ProfileReport {
    pub age: u8,
    pub reference_sex: ReferenceSex,
}

#[derive(Debug, Default, Serialize)]
pub struct Coverage {
    pub first_date: Option<NaiveDate>,
    pub last_date: Option<NaiveDate>,
    pub heart_rate_samples: usize,
    pub strain_days: usize,
    pub step_days: usize,
    pub sleep_days: usize,
    pub resting_heart_rate_days: usize,
    pub hrv_days: usize,
    pub vo2_days: usize,
}

#[derive(Debug, Serialize)]
pub struct MetricSummary {
    pub latest_daily_strain: Option<f64>,
    pub recent_load: Option<RecentLoad>,
    pub fitness_age: Option<FitnessAge>,
    pub personal_readiness: Option<PersonalReadiness>,
    pub sleep_opportunity: Option<SleepOpportunity>,
    pub strain_sleep_response: Option<StrainSleepResponse>,
    pub recovery_lag: Option<RecoveryLag>,
    pub sleep_regularity: Option<SleepRegularity>,
}

#[derive(Debug, Serialize)]
pub struct PersonalReadiness {
    pub date: NaiveDate,
    pub score: f64,
    pub physiological_score: f64,
    pub expected_strain_capacity: f64,
    pub analog_count: usize,
    pub mean_similarity_percent: f64,
    pub analog_days: Vec<AnalogDay>,
    pub interpretation: &'static str,
}

#[derive(Debug, Serialize)]
pub struct AnalogDay {
    pub date: NaiveDate,
    pub similarity_percent: f64,
    pub completed_strain: f64,
}

#[derive(Debug, Serialize)]
pub struct SleepOpportunity {
    pub target_recovery_score: f64,
    pub estimated_hours: f64,
    pub observed_lower_bound_hours: f64,
    pub observed_upper_bound_hours: f64,
    pub training_days: usize,
    pub model_r_squared: f64,
    pub interpretation: &'static str,
}

#[derive(Debug, Serialize)]
pub struct RecentLoad {
    pub last_7_day_average: f64,
    pub prior_28_day_average: f64,
    pub ratio: f64,
    pub interpretation: &'static str,
}

#[derive(Debug, Serialize)]
pub struct FitnessAge {
    pub estimated_years: f64,
    pub chronological_age: u8,
    pub difference_years: f64,
    pub vo2_max_used: f64,
    pub observation_count: usize,
    pub reference_sex: ReferenceSex,
    pub interpretation: String,
}

#[derive(Debug, Serialize)]
pub struct StrainSleepResponse {
    pub paired_days: usize,
    pub correlation: f64,
    pub high_strain_threshold: f64,
    pub high_vs_typical_sleep_delta_hours: f64,
    pub interpretation: &'static str,
}

#[derive(Debug, Serialize)]
pub struct RecoveryLag {
    pub hard_days_analyzed: usize,
    pub hard_day_threshold: f64,
    pub median_days_to_baseline: f64,
    pub interpretation: &'static str,
}

#[derive(Debug, Serialize)]
pub struct SleepRegularity {
    pub score: f64,
    pub midpoint_variability_minutes: f64,
    pub nights: usize,
    pub interpretation: &'static str,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct DailyMetric {
    pub date: NaiveDate,
    pub strain: Option<f64>,
    pub load_units: Option<f64>,
    pub observed_hours: Option<f64>,
    pub active_minutes: Option<f64>,
    pub zone_2_plus_minutes: Option<f64>,
    pub steps: Option<u64>,
    pub sleep_hours: Option<f64>,
    pub sleep_midpoint_minute: Option<f64>,
    pub resting_heart_rate: Option<f64>,
    pub hrv_milliseconds: Option<f64>,
    pub vo2_max: Option<f64>,
}

#[derive(Clone, Debug)]
struct HeartSample {
    timestamp: NaiveDateTime,
    utc_offset_seconds: i64,
    beats_per_minute: f64,
}

#[derive(Clone, Debug, Default)]
struct StrainAccumulator {
    load_units: f64,
    observed_minutes: f64,
    active_minutes: f64,
    zone_2_plus_minutes: f64,
}

#[derive(Clone, Debug)]
struct SleepNight {
    hours: f64,
    midpoint_minute: f64,
}

pub fn analyze_database(path: &Path, profile: MetricProfile) -> Result<DashboardReport> {
    if !path.is_file() {
        bail!("DuckDB database does not exist: {}", path.display());
    }

    let config = Config::default()
        .access_mode(AccessMode::ReadOnly)?
        .enable_external_access(false)?;
    let connection = Connection::open_with_flags(path, config)
        .with_context(|| format!("could not open {} read-only", path.display()))?;

    analyze_connection(&connection, profile)
}

pub fn analyze_parquet(directory: &Path, profile: MetricProfile) -> Result<DashboardReport> {
    if !directory.is_dir() {
        bail!(
            "Parquet analytics directory does not exist: {}",
            directory.display()
        );
    }

    let config = Config::default().enable_external_access(true)?;
    let connection = Connection::open_in_memory_with_flags(config)
        .context("could not open the in-memory DuckDB analytics connection")?;
    for table in ANALYTICS_TABLES {
        let path = directory.join(format!("{table}.parquet"));
        if !path.is_file() {
            bail!("Parquet analytics snapshot is missing: {}", path.display());
        }
        let canonical_path = path
            .canonicalize()
            .with_context(|| format!("could not resolve {}", path.display()))?;
        let escaped_path = canonical_path.to_string_lossy().replace('\'', "''");
        connection
            .execute_batch(&format!(
                "CREATE VIEW \"{table}\" AS SELECT * FROM read_parquet('{escaped_path}')"
            ))
            .with_context(|| format!("could not register {}", path.display()))?;
    }

    analyze_connection(&connection, profile)
}

fn analyze_connection(connection: &Connection, profile: MetricProfile) -> Result<DashboardReport> {
    let heart_samples = load_heart_samples(connection)?;
    let resting_heart_rate =
        load_daily_average(connection, "daily_resting_heart_rate", "beats_per_minute")?;
    let hrv = load_daily_average(
        connection,
        "daily_heart_rate_variability",
        "average_hrv_milliseconds",
    )?;
    let vo2 = load_daily_average(connection, "daily_vo2_max", "vo2_max")?;
    let steps = load_daily_steps(connection)?;
    let sleep = load_sleep(connection)?;
    let strain = calculate_strain(&heart_samples, &resting_heart_rate, profile.age);

    let all_dates: BTreeSet<NaiveDate> = strain
        .keys()
        .chain(steps.keys())
        .chain(sleep.keys())
        .chain(resting_heart_rate.keys())
        .chain(hrv.keys())
        .chain(vo2.keys())
        .copied()
        .collect();

    let daily: Vec<DailyMetric> = all_dates
        .iter()
        .map(|date| {
            let strain_day = strain.get(date);
            let sleep_night = sleep.get(date);
            DailyMetric {
                date: *date,
                strain: strain_day.map(|value| strain_score(value.load_units)),
                load_units: strain_day.map(|value| round(value.load_units, 2)),
                observed_hours: strain_day.map(|value| round(value.observed_minutes / 60.0, 2)),
                active_minutes: strain_day.map(|value| round(value.active_minutes, 1)),
                zone_2_plus_minutes: strain_day.map(|value| round(value.zone_2_plus_minutes, 1)),
                steps: steps.get(date).copied(),
                sleep_hours: sleep_night.map(|value| round(value.hours, 2)),
                sleep_midpoint_minute: sleep_night.map(|value| round(value.midpoint_minute, 1)),
                resting_heart_rate: resting_heart_rate.get(date).map(|value| round(*value, 1)),
                hrv_milliseconds: hrv.get(date).map(|value| round(*value, 1)),
                vo2_max: vo2.get(date).map(|value| round(*value, 1)),
            }
        })
        .collect();

    let latest_daily_strain = daily.iter().rev().find_map(|day| day.strain);
    let recovery_scores = calculate_physiological_recovery(&daily);
    let coverage = Coverage {
        first_date: all_dates.first().copied(),
        last_date: all_dates.last().copied(),
        heart_rate_samples: heart_samples.len(),
        strain_days: strain.len(),
        step_days: steps.len(),
        sleep_days: sleep.len(),
        resting_heart_rate_days: resting_heart_rate.len(),
        hrv_days: hrv.len(),
        vo2_days: vo2.len(),
    };

    Ok(DashboardReport {
        generated_at: Utc::now().to_rfc3339(),
        profile: ProfileReport {
            age: profile.age,
            reference_sex: profile.reference_sex,
        },
        coverage,
        summary: MetricSummary {
            latest_daily_strain,
            recent_load: calculate_recent_load(&daily),
            fitness_age: calculate_fitness_age(&vo2, profile),
            personal_readiness: calculate_personal_readiness(
                &daily,
                &recovery_scores,
            ),
            sleep_opportunity: calculate_sleep_opportunity(
                &daily,
                &recovery_scores,
            ),
            strain_sleep_response: calculate_strain_sleep_response(&daily),
            recovery_lag: calculate_recovery_lag(&daily),
            sleep_regularity: calculate_sleep_regularity(&daily),
        },
        daily,
        methodology: vec![
            "Daily strain integrates heart-rate reserve over observed time and maps the result to a transparent 0-21 logarithmic scale with 1,000 load units as the ceiling reference; gaps over two minutes contribute no load.".to_owned(),
            "Fitness age inverts a population VO2 max reference equation. It is an exploratory estimate, not WHOOP's proprietary metric and not a medical assessment.".to_owned(),
            "Personal readiness compares today's sleep, RHR, HRV, and prior-day strain with similar historical mornings; its physiology component uses rolling personal baselines.".to_owned(),
            "Sleep opportunity is a bounded regression estimate from prior strain, sleep, and next-morning physiology. It is an association in this dataset, not a prescription.".to_owned(),
            "Response metrics describe associations in your own history. They do not establish that strain caused a sleep or recovery change.".to_owned(),
        ],
    })
}

fn load_heart_samples(connection: &Connection) -> Result<Vec<HeartSample>> {
    let mut statement = connection.prepare(
        "SELECT sample_time, COALESCE(utc_offset_seconds, 0)::BIGINT, \
                AVG(beats_per_minute)::DOUBLE \
         FROM heart_rate \
         WHERE beats_per_minute BETWEEN 25 AND 240 \
         GROUP BY sample_time, COALESCE(utc_offset_seconds, 0) \
         ORDER BY sample_time",
    )?;
    let rows = statement.query_map([], |row| {
        Ok(HeartSample {
            timestamp: row.get(0)?,
            utc_offset_seconds: row.get(1)?,
            beats_per_minute: row.get(2)?,
        })
    })?;
    rows.collect::<duckdb::Result<Vec<_>>>().map_err(Into::into)
}

fn load_daily_average(
    connection: &Connection,
    table: &str,
    value: &str,
) -> Result<BTreeMap<NaiveDate, f64>> {
    let allowed = [
        ("daily_resting_heart_rate", "beats_per_minute"),
        ("daily_heart_rate_variability", "average_hrv_milliseconds"),
        ("daily_vo2_max", "vo2_max"),
    ];
    if !allowed.contains(&(table, value)) {
        bail!("untrusted daily metric query: {table}.{value}");
    }
    let query = format!(
        "SELECT observation_date, AVG({value})::DOUBLE \
         FROM {table} WHERE {value} IS NOT NULL GROUP BY observation_date"
    );
    let mut statement = connection.prepare(&query)?;
    let rows = statement.query_map([], |row| {
        Ok((row.get::<_, NaiveDate>(0)?, row.get::<_, f64>(1)?))
    })?;
    rows.collect::<duckdb::Result<BTreeMap<_, _>>>()
        .map_err(Into::into)
}

fn load_daily_steps(connection: &Connection) -> Result<BTreeMap<NaiveDate, u64>> {
    let mut statement = connection.prepare(
        "WITH deduplicated AS ( \
             SELECT CAST(start_time + COALESCE(start_utc_offset_seconds, 0) * \
                         INTERVAL 1 SECOND AS DATE) AS local_date, \
                    start_time, end_time, MAX(count)::UBIGINT AS interval_steps \
             FROM steps \
             GROUP BY local_date, start_time, end_time \
         ) \
         SELECT local_date, SUM(interval_steps)::UBIGINT \
         FROM deduplicated GROUP BY local_date",
    )?;
    let rows = statement.query_map([], |row| {
        Ok((row.get::<_, NaiveDate>(0)?, row.get::<_, u64>(1)?))
    })?;
    rows.collect::<duckdb::Result<BTreeMap<_, _>>>()
        .map_err(Into::into)
}

fn load_sleep(connection: &Connection) -> Result<BTreeMap<NaiveDate, SleepNight>> {
    let mut statement = connection.prepare(
        "SELECT start_time, COALESCE(start_utc_offset_seconds, 0)::BIGINT, \
                end_time, COALESCE(end_utc_offset_seconds, 0)::BIGINT, \
                minutes_asleep \
         FROM sleep \
         WHERE COALESCE(is_nap, FALSE) = FALSE \
           AND COALESCE(is_main_sleep, TRUE) = TRUE \
         ORDER BY end_time",
    )?;
    let rows = statement.query_map([], |row| {
        Ok((
            row.get::<_, NaiveDateTime>(0)?,
            row.get::<_, i64>(1)?,
            row.get::<_, NaiveDateTime>(2)?,
            row.get::<_, i64>(3)?,
            row.get::<_, Option<i64>>(4)?,
        ))
    })?;

    let mut nights = BTreeMap::new();
    for row in rows {
        let (start, start_offset, end, end_offset, minutes_asleep) = row?;
        let local_start = start + Duration::seconds(start_offset);
        let local_end = end + Duration::seconds(end_offset);
        let interval_minutes = (end - start).num_seconds() as f64 / 60.0;
        let asleep_minutes = minutes_asleep
            .filter(|value| *value > 0)
            .map(|value| value as f64)
            .unwrap_or(interval_minutes);
        if !(30.0..=1_440.0).contains(&asleep_minutes) {
            continue;
        }
        let midpoint = local_start + (local_end - local_start) / 2;
        let candidate = SleepNight {
            hours: asleep_minutes / 60.0,
            midpoint_minute: midpoint.hour() as f64 * 60.0
                + midpoint.minute() as f64
                + midpoint.second() as f64 / 60.0,
        };
        nights
            .entry(local_end.date())
            .and_modify(|existing: &mut SleepNight| {
                if candidate.hours > existing.hours {
                    *existing = candidate.clone();
                }
            })
            .or_insert(candidate);
    }
    Ok(nights)
}

fn calculate_strain(
    samples: &[HeartSample],
    resting: &BTreeMap<NaiveDate, f64>,
    age: u8,
) -> BTreeMap<NaiveDate, StrainAccumulator> {
    let fallback_resting = median(resting.values().copied().collect()).unwrap_or(60.0);
    let max_heart_rate = 208.0 - 0.7 * f64::from(age);
    let mut result: BTreeMap<NaiveDate, StrainAccumulator> = BTreeMap::new();

    for pair in samples.windows(2) {
        let current = &pair[0];
        let next = &pair[1];
        let seconds = (next.timestamp - current.timestamp).num_seconds();
        if seconds <= 0 || seconds > MAX_SAMPLE_GAP_SECONDS {
            continue;
        }
        let local_time = current.timestamp + Duration::seconds(current.utc_offset_seconds);
        let next_local_time = next.timestamp + Duration::seconds(next.utc_offset_seconds);
        if local_time.date() != next_local_time.date() {
            continue;
        }

        let date = local_time.date();
        let resting_heart_rate = resting.get(&date).copied().unwrap_or(fallback_resting);
        let reserve = (max_heart_rate - resting_heart_rate).max(1.0);
        let fraction = ((current.beats_per_minute - resting_heart_rate) / reserve).clamp(0.0, 1.2);
        let minutes = seconds as f64 / 60.0;
        let day = result.entry(date).or_default();
        day.observed_minutes += minutes;
        if fraction >= 0.30 {
            day.active_minutes += minutes;
        }
        if fraction >= 0.50 {
            day.zone_2_plus_minutes += minutes;
        }
        day.load_units += minutes * fraction.powi(2) * 10.0;
    }
    result
}

fn strain_score(load_units: f64) -> f64 {
    round(
        (21.0 * (1.0 + load_units / 10.0).ln() / 101.0_f64.ln()).clamp(0.0, 21.0),
        1,
    )
}

fn calculate_fitness_age(
    vo2: &BTreeMap<NaiveDate, f64>,
    profile: MetricProfile,
) -> Option<FitnessAge> {
    let latest = vo2.keys().next_back()?;
    let start = *latest - Duration::days(29);
    let recent: Vec<f64> = vo2
        .range(start..=*latest)
        .map(|(_, value)| *value)
        .filter(|value| value.is_finite() && *value > 0.0)
        .collect();
    if recent.is_empty() {
        return None;
    }
    let vo2_used = mean(&recent)?;
    let raw_age = match profile.reference_sex {
        ReferenceSex::Male => (58.0 - vo2_used) / 0.42,
        ReferenceSex::Female => (46.0 - vo2_used) / 0.35,
    };
    let estimated_years = raw_age.clamp(FITNESS_AGE_MIN, FITNESS_AGE_MAX);
    let difference = estimated_years - f64::from(profile.age);
    let interpretation = if difference <= -2.0 {
        format!(
            "about {:.0} years younger than the selected reference",
            -difference
        )
    } else if difference >= 2.0 {
        format!(
            "about {:.0} years older than the selected reference",
            difference
        )
    } else {
        "close to the selected age reference".to_owned()
    };
    Some(FitnessAge {
        estimated_years: round(estimated_years, 1),
        chronological_age: profile.age,
        difference_years: round(difference, 1),
        vo2_max_used: round(vo2_used, 1),
        observation_count: recent.len(),
        reference_sex: profile.reference_sex,
        interpretation,
    })
}

fn calculate_recent_load(daily: &[DailyMetric]) -> Option<RecentLoad> {
    let strain_days: Vec<f64> = daily.iter().filter_map(|day| day.strain).collect();
    if strain_days.len() < 14 {
        return None;
    }
    let split = strain_days.len().saturating_sub(7);
    let recent = mean(&strain_days[split..])?;
    let prior_start = split.saturating_sub(28);
    let prior = mean(&strain_days[prior_start..split])?;
    if prior <= 0.0 {
        return None;
    }
    let ratio = recent / prior;
    let interpretation = if ratio > 1.20 {
        "recent strain is clearly above your preceding baseline"
    } else if ratio < 0.80 {
        "recent strain is clearly below your preceding baseline"
    } else {
        "recent strain is near your preceding baseline"
    };
    Some(RecentLoad {
        last_7_day_average: round(recent, 1),
        prior_28_day_average: round(prior, 1),
        ratio: round(ratio, 2),
        interpretation,
    })
}

fn calculate_physiological_recovery(daily: &[DailyMetric]) -> BTreeMap<NaiveDate, f64> {
    let mut scores = BTreeMap::new();
    for (index, day) in daily.iter().enumerate() {
        let (Some(rhr), Some(hrv)) = (day.resting_heart_rate, day.hrv_milliseconds) else {
            continue;
        };
        let baseline_start = index.saturating_sub(28);
        let prior = &daily[baseline_start..index];
        let baseline_rhr = median(
            prior
                .iter()
                .filter_map(|value| value.resting_heart_rate)
                .collect(),
        );
        let baseline_hrv = median(
            prior
                .iter()
                .filter_map(|value| value.hrv_milliseconds)
                .collect(),
        );
        let baseline_observations = prior
            .iter()
            .filter(|value| value.resting_heart_rate.is_some() && value.hrv_milliseconds.is_some())
            .count();
        let (Some(baseline_rhr), Some(baseline_hrv)) = (baseline_rhr, baseline_hrv) else {
            continue;
        };
        if baseline_observations < 7 || baseline_rhr <= 0.0 || baseline_hrv <= 0.0 {
            continue;
        }
        let hrv_change = hrv / baseline_hrv - 1.0;
        let rhr_change = baseline_rhr / rhr - 1.0;
        let score = (50.0 + 100.0 * (0.60 * hrv_change + 0.40 * rhr_change)).clamp(0.0, 100.0);
        scores.insert(day.date, round(score, 1));
    }
    scores
}

fn calculate_personal_readiness(
    daily: &[DailyMetric],
    recovery_scores: &BTreeMap<NaiveDate, f64>,
) -> Option<PersonalReadiness> {
    #[derive(Clone)]
    struct Candidate {
        date: NaiveDate,
        features: [f64; 4],
        physiological_score: f64,
        completed_strain: f64,
    }

    let by_date: BTreeMap<NaiveDate, &DailyMetric> =
        daily.iter().map(|day| (day.date, day)).collect();
    let candidates: Vec<Candidate> = daily
        .iter()
        .filter_map(|day| {
            let previous_strain = by_date.get(&(day.date - Duration::days(1)))?.strain?;
            Some(Candidate {
                date: day.date,
                features: [
                    day.sleep_hours?,
                    day.resting_heart_rate?,
                    day.hrv_milliseconds?,
                    previous_strain,
                ],
                physiological_score: *recovery_scores.get(&day.date)?,
                completed_strain: day.strain?,
            })
        })
        .collect();
    if candidates.len() < 15 {
        return None;
    }
    let current = candidates.last()?.clone();
    let history = &candidates[..candidates.len() - 1];
    let feature_rows: Vec<[f64; 4]> = history.iter().map(|candidate| candidate.features).collect();
    let means = feature_means(&feature_rows);
    let scales = feature_scales(&feature_rows, means);
    let mut analogs: Vec<(f64, &Candidate)> = history
        .iter()
        .map(|candidate| {
            let squared_distance: f64 = candidate
                .features
                .iter()
                .zip(current.features)
                .enumerate()
                .map(|(index, (historical, current_value))| {
                    ((historical - current_value) / scales[index]).powi(2)
                })
                .sum();
            let distance = squared_distance.sqrt();
            ((-distance / 2.0).exp(), candidate)
        })
        .collect();
    analogs.sort_by(|left, right| right.0.partial_cmp(&left.0).unwrap_or(Ordering::Equal));
    analogs.truncate(5);
    let total_weight: f64 = analogs.iter().map(|value| value.0).sum();
    if total_weight <= 0.0 {
        return None;
    }
    let expected_strain = analogs
        .iter()
        .map(|(weight, candidate)| weight * candidate.completed_strain)
        .sum::<f64>()
        / total_weight;
    let mean_similarity =
        analogs.iter().map(|value| value.0).sum::<f64>() / analogs.len() as f64 * 100.0;

    let baseline_sleep = median(
        history
            .iter()
            .rev()
            .take(28)
            .map(|candidate| candidate.features[0])
            .collect(),
    )?;
    let sleep_component = if baseline_sleep > 0.0 {
        (50.0 + 100.0 * (current.features[0] / baseline_sleep - 1.0)).clamp(0.0, 100.0)
    } else {
        50.0
    };
    let readiness = (0.80 * current.physiological_score + 0.20 * sleep_component).clamp(0.0, 100.0);
    let interpretation = if readiness >= 67.0 {
        "current morning signals are favorable relative to your own recent baseline"
    } else if readiness >= 40.0 {
        "current morning signals are mixed relative to your own recent baseline"
    } else {
        "current morning signals are below your own recent baseline"
    };
    Some(PersonalReadiness {
        date: current.date,
        score: round(readiness, 0),
        physiological_score: round(current.physiological_score, 0),
        expected_strain_capacity: round(expected_strain, 1),
        analog_count: analogs.len(),
        mean_similarity_percent: round(mean_similarity, 0),
        analog_days: analogs
            .into_iter()
            .map(|(weight, candidate)| AnalogDay {
                date: candidate.date,
                similarity_percent: round(weight * 100.0, 0),
                completed_strain: round(candidate.completed_strain, 1),
            })
            .collect(),
        interpretation,
    })
}

fn feature_means(features: &[[f64; 4]]) -> [f64; 4] {
    let mut sums = [0.0; 4];
    for row in features {
        for (index, value) in row.iter().enumerate() {
            sums[index] += value;
        }
    }
    sums.map(|sum| sum / features.len() as f64)
}

fn feature_scales(features: &[[f64; 4]], means: [f64; 4]) -> [f64; 4] {
    let mut squared = [0.0; 4];
    for row in features {
        for (index, value) in row.iter().enumerate() {
            squared[index] += (value - means[index]).powi(2);
        }
    }
    squared.map(|sum| (sum / features.len() as f64).sqrt().max(0.001))
}

fn calculate_sleep_opportunity(
    daily: &[DailyMetric],
    recovery_scores: &BTreeMap<NaiveDate, f64>,
) -> Option<SleepOpportunity> {
    let by_date: BTreeMap<NaiveDate, &DailyMetric> =
        daily.iter().map(|day| (day.date, day)).collect();
    let training: Vec<(f64, f64, f64)> = recovery_scores
        .iter()
        .filter_map(|(date, recovery)| {
            let current = by_date.get(date)?;
            let previous = by_date.get(&(*date - Duration::days(1)))?;
            Some((previous.strain?, current.sleep_hours?, *recovery))
        })
        .collect();
    if training.len() < 30 {
        return None;
    }
    let strain_values: Vec<f64> = training.iter().map(|row| row.0).collect();
    let sleep_values: Vec<f64> = training.iter().map(|row| row.1).collect();
    let recovery_values: Vec<f64> = training.iter().map(|row| row.2).collect();
    let strain_mean = mean(&strain_values)?;
    let sleep_mean = mean(&sleep_values)?;
    let recovery_mean = mean(&recovery_values)?;
    let strain_scale = standard_deviation(&strain_values, strain_mean)?.max(0.001);
    let sleep_scale = standard_deviation(&sleep_values, sleep_mean)?.max(0.001);

    let standardized: Vec<(f64, f64, f64)> = training
        .iter()
        .map(|(strain, sleep, recovery)| {
            (
                (strain - strain_mean) / strain_scale,
                (sleep - sleep_mean) / sleep_scale,
                *recovery,
            )
        })
        .collect();
    let (strain_coefficient, sleep_coefficient) = ridge_two_predictors(&standardized, 0.5)?;
    if sleep_coefficient <= 0.0 {
        return None;
    }
    let latest_strain = daily.iter().rev().find_map(|day| day.strain)?;
    let target = 70.0;
    let standardized_strain = (latest_strain - strain_mean) / strain_scale;
    let required_standardized_sleep =
        (target - recovery_mean - strain_coefficient * standardized_strain) / sleep_coefficient;
    let unconstrained_hours = sleep_mean + required_standardized_sleep * sleep_scale;
    let lower = percentile(sleep_values.clone(), 0.10)?.max(4.0);
    let upper = percentile(sleep_values.clone(), 0.90)?.min(10.5);
    let estimated_hours = unconstrained_hours.clamp(lower, upper);
    let predictions: Vec<f64> = standardized
        .iter()
        .map(|(strain, sleep, _)| {
            recovery_mean + strain_coefficient * strain + sleep_coefficient * sleep
        })
        .collect();
    let r_squared = coefficient_of_determination(&recovery_values, &predictions)?;
    Some(SleepOpportunity {
        target_recovery_score: target,
        estimated_hours: round(estimated_hours, 1),
        observed_lower_bound_hours: round(lower, 1),
        observed_upper_bound_hours: round(upper, 1),
        training_days: training.len(),
        model_r_squared: round(r_squared, 2),
        interpretation: "sleep opportunity associated with a 70/100 next-morning physiology score after the latest strain; bounded to your observed 10th-90th percentile",
    })
}

fn ridge_two_predictors(rows: &[(f64, f64, f64)], lambda: f64) -> Option<(f64, f64)> {
    let mut x1_x1 = lambda;
    let mut x1_x2 = 0.0;
    let mut x2_x2 = lambda;
    let mut x1_y = 0.0;
    let mut x2_y = 0.0;
    let y_mean = mean(&rows.iter().map(|row| row.2).collect::<Vec<_>>())?;
    for (x1, x2, y) in rows {
        x1_x1 += x1 * x1;
        x1_x2 += x1 * x2;
        x2_x2 += x2 * x2;
        x1_y += x1 * (y - y_mean);
        x2_y += x2 * (y - y_mean);
    }
    let determinant = x1_x1 * x2_x2 - x1_x2.powi(2);
    if determinant.abs() < 1e-9 {
        return None;
    }
    Some((
        (x1_y * x2_x2 - x2_y * x1_x2) / determinant,
        (x2_y * x1_x1 - x1_y * x1_x2) / determinant,
    ))
}

fn coefficient_of_determination(actual: &[f64], predicted: &[f64]) -> Option<f64> {
    if actual.len() != predicted.len() || actual.is_empty() {
        return None;
    }
    let actual_mean = mean(actual)?;
    let residual: f64 = actual
        .iter()
        .zip(predicted)
        .map(|(actual, predicted)| (actual - predicted).powi(2))
        .sum();
    let total: f64 = actual
        .iter()
        .map(|actual| (actual - actual_mean).powi(2))
        .sum();
    if total <= 0.0 {
        return None;
    }
    Some((1.0 - residual / total).clamp(-1.0, 1.0))
}

fn calculate_strain_sleep_response(daily: &[DailyMetric]) -> Option<StrainSleepResponse> {
    let by_date: BTreeMap<NaiveDate, &DailyMetric> =
        daily.iter().map(|day| (day.date, day)).collect();
    let pairs: Vec<(f64, f64)> = daily
        .iter()
        .filter_map(|day| {
            Some((
                day.strain?,
                by_date.get(&(day.date + Duration::days(1)))?.sleep_hours?,
            ))
        })
        .collect();
    if pairs.len() < 14 {
        return None;
    }
    let strains: Vec<f64> = pairs.iter().map(|pair| pair.0).collect();
    let sleep_hours: Vec<f64> = pairs.iter().map(|pair| pair.1).collect();
    let threshold = percentile(strains.clone(), 0.75)?;
    let high: Vec<f64> = pairs
        .iter()
        .filter(|pair| pair.0 >= threshold)
        .map(|pair| pair.1)
        .collect();
    let typical: Vec<f64> = pairs
        .iter()
        .filter(|pair| pair.0 < threshold)
        .map(|pair| pair.1)
        .collect();
    let delta = mean(&high)? - mean(&typical)?;
    let correlation = pearson(&strains, &sleep_hours)?;
    let interpretation = if delta <= -0.25 {
        "your high-strain days have recently been followed by less sleep"
    } else if delta >= 0.25 {
        "your high-strain days have recently been followed by more sleep"
    } else {
        "next-night sleep duration has been similar after high and typical strain"
    };
    Some(StrainSleepResponse {
        paired_days: pairs.len(),
        correlation: round(correlation, 2),
        high_strain_threshold: round(threshold, 1),
        high_vs_typical_sleep_delta_hours: round(delta, 2),
        interpretation,
    })
}

fn calculate_recovery_lag(daily: &[DailyMetric]) -> Option<RecoveryLag> {
    let strains: Vec<f64> = daily.iter().filter_map(|day| day.strain).collect();
    if strains.len() < 28 {
        return None;
    }
    let threshold = percentile(strains, 0.75)?;
    let by_date: BTreeMap<NaiveDate, &DailyMetric> =
        daily.iter().map(|day| (day.date, day)).collect();
    let mut recovery_days = Vec::new();

    for day in daily
        .iter()
        .filter(|day| day.strain.unwrap_or(-1.0) >= threshold)
    {
        let baseline_start = day.date - Duration::days(28);
        let prior: Vec<&DailyMetric> = daily
            .iter()
            .filter(|candidate| candidate.date >= baseline_start && candidate.date < day.date)
            .collect();
        let baseline_rhr = median(
            prior
                .iter()
                .filter_map(|value| value.resting_heart_rate)
                .collect(),
        );
        let baseline_hrv = median(
            prior
                .iter()
                .filter_map(|value| value.hrv_milliseconds)
                .collect(),
        );
        let (Some(baseline_rhr), Some(baseline_hrv)) = (baseline_rhr, baseline_hrv) else {
            continue;
        };
        for lag in 1..=3 {
            let Some(candidate) = by_date.get(&(day.date + Duration::days(lag))) else {
                continue;
            };
            let recovered = candidate
                .resting_heart_rate
                .zip(candidate.hrv_milliseconds)
                .is_some_and(|(rhr, hrv)| rhr <= baseline_rhr + 1.0 && hrv >= baseline_hrv * 0.95);
            if recovered {
                recovery_days.push(lag as f64);
                break;
            }
        }
    }
    let median_days = median(recovery_days.clone())?;
    Some(RecoveryLag {
        hard_days_analyzed: recovery_days.len(),
        hard_day_threshold: round(threshold, 1),
        median_days_to_baseline: round(median_days, 1),
        interpretation: "median time for both resting heart rate and HRV to return near their preceding 28-day baselines",
    })
}

fn calculate_sleep_regularity(daily: &[DailyMetric]) -> Option<SleepRegularity> {
    let midpoints: Vec<f64> = daily
        .iter()
        .rev()
        .filter_map(|day| day.sleep_midpoint_minute)
        .take(30)
        .collect();
    if midpoints.len() < 7 {
        return None;
    }
    let count = midpoints.len() as f64;
    let mean_sin = midpoints
        .iter()
        .map(|minute| (TAU * minute / 1_440.0).sin())
        .sum::<f64>()
        / count;
    let mean_cos = midpoints
        .iter()
        .map(|minute| (TAU * minute / 1_440.0).cos())
        .sum::<f64>()
        / count;
    let resultant = mean_sin.hypot(mean_cos).clamp(f64::MIN_POSITIVE, 1.0);
    let variability = (-2.0 * resultant.ln()).sqrt() * 1_440.0 / TAU;
    let score = 100.0 * (-variability / 120.0).exp();
    let interpretation = if variability <= 30.0 {
        "your recent sleep timing is very consistent"
    } else if variability <= 60.0 {
        "your recent sleep timing is moderately consistent"
    } else {
        "your recent sleep timing varies substantially"
    };
    Some(SleepRegularity {
        score: round(score, 0),
        midpoint_variability_minutes: round(variability, 0),
        nights: midpoints.len(),
        interpretation,
    })
}

fn mean(values: &[f64]) -> Option<f64> {
    (!values.is_empty()).then(|| values.iter().sum::<f64>() / values.len() as f64)
}

fn standard_deviation(values: &[f64], average: f64) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    Some(
        (values
            .iter()
            .map(|value| (value - average).powi(2))
            .sum::<f64>()
            / values.len() as f64)
            .sqrt(),
    )
}

fn median(mut values: Vec<f64>) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(Ordering::Equal));
    let middle = values.len() / 2;
    if values.len() % 2 == 0 {
        Some((values[middle - 1] + values[middle]) / 2.0)
    } else {
        Some(values[middle])
    }
}

fn percentile(mut values: Vec<f64>, fraction: f64) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(Ordering::Equal));
    let position = fraction.clamp(0.0, 1.0) * (values.len() - 1) as f64;
    let low = position.floor() as usize;
    let high = position.ceil() as usize;
    let weight = position - low as f64;
    Some(values[low] * (1.0 - weight) + values[high] * weight)
}

fn pearson(left: &[f64], right: &[f64]) -> Option<f64> {
    if left.len() != right.len() || left.len() < 2 {
        return None;
    }
    let left_mean = mean(left)?;
    let right_mean = mean(right)?;
    let numerator: f64 = left
        .iter()
        .zip(right)
        .map(|(a, b)| (a - left_mean) * (b - right_mean))
        .sum();
    let left_scale: f64 = left.iter().map(|value| (value - left_mean).powi(2)).sum();
    let right_scale: f64 = right.iter().map(|value| (value - right_mean).powi(2)).sum();
    let denominator = (left_scale * right_scale).sqrt();
    (denominator > 0.0).then(|| numerator / denominator)
}

fn round(value: f64, digits: i32) -> f64 {
    let factor = 10_f64.powi(digits);
    (value * factor).round() / factor
}

#[cfg(test)]
mod tests {
    use super::*;

    fn date(day: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(2026, 8, day).unwrap()
    }

    #[test]
    fn strain_scale_is_bounded_and_monotonic() {
        assert_eq!(strain_score(0.0), 0.0);
        assert!(strain_score(50.0) < strain_score(200.0));
        assert!(strain_score(300.0) < 21.0);
        assert_eq!(strain_score(1_000.0), 21.0);
        assert_eq!(strain_score(10_000.0), 21.0);
    }

    #[test]
    fn fitness_age_uses_recent_vo2_and_selected_reference() {
        let vo2 = BTreeMap::from([(date(1), 45.4), (date(2), 45.8)]);
        let metric = calculate_fitness_age(
            &vo2,
            MetricProfile {
                age: 30,
                reference_sex: ReferenceSex::Male,
            },
        )
        .unwrap();
        assert!((metric.estimated_years - 29.5).abs() < 0.2);
        assert_eq!(metric.observation_count, 2);
    }

    #[test]
    fn circular_sleep_variability_handles_midnight() {
        let mut daily = Vec::new();
        for (day, midpoint) in [1_430.0, 10.0, 0.0, 1_435.0, 5.0, 15.0, 1_425.0]
            .into_iter()
            .enumerate()
        {
            daily.push(DailyMetric {
                date: date(day as u32 + 1),
                sleep_midpoint_minute: Some(midpoint),
                ..DailyMetric::default()
            });
        }
        let regularity = calculate_sleep_regularity(&daily).unwrap();
        assert!(regularity.midpoint_variability_minutes < 20.0);
    }

    #[test]
    fn response_metric_requires_fourteen_pairs() {
        let daily = (1..=10)
            .map(|day| DailyMetric {
                date: date(day),
                strain: Some(day as f64),
                sleep_hours: Some(7.0),
                ..DailyMetric::default()
            })
            .collect::<Vec<_>>();
        assert!(calculate_strain_sleep_response(&daily).is_none());
    }
}
