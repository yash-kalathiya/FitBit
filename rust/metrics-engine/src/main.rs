use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use anyhow::{Context, Result, bail};
use fitbit_metrics::{MetricProfile, ReferenceSex, analyze_database, analyze_parquet};

enum MetricsInput {
    ParquetDirectory(PathBuf),
    Database(PathBuf),
}

fn parse_arguments() -> Result<(MetricsInput, MetricProfile)> {
    let mut database_argument = None;
    let mut parquet_argument = None;
    let mut age = env::var("FITBIT_AGE")
        .ok()
        .map(|value| value.parse::<u8>().context("FITBIT_AGE must be an integer"))
        .transpose()?
        .unwrap_or(30);
    let mut reference_sex = env::var("FITBIT_REFERENCE_SEX")
        .unwrap_or_else(|_| "male".to_owned())
        .parse::<ReferenceSex>()?;

    let mut arguments = env::args().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--database" => {
                database_argument = Some(PathBuf::from(
                    arguments.next().context("--database requires a path")?,
                ));
            }
            "--parquet-directory" => {
                parquet_argument = Some(PathBuf::from(
                    arguments
                        .next()
                        .context("--parquet-directory requires a path")?,
                ));
            }
            "--age" => {
                age = arguments
                    .next()
                    .context("--age requires a value")?
                    .parse()
                    .context("--age must be an integer")?;
            }
            "--reference-sex" => {
                reference_sex = arguments
                    .next()
                    .context("--reference-sex requires male or female")?
                    .parse()?;
            }
            "--help" | "-h" => {
                println!(
                    "Usage: fitbit-metrics [--parquet-directory PATH | --database PATH] \\\n                     [--age 30] [--reference-sex male|female]"
                );
                std::process::exit(0);
            }
            unknown => bail!("unknown argument: {unknown}"),
        }
    }

    if !(18..=100).contains(&age) {
        bail!("--age must be between 18 and 100");
    }
    if database_argument.is_some() && parquet_argument.is_some() {
        bail!("use only one of --parquet-directory or --database");
    }
    let input = if let Some(directory) = parquet_argument {
        MetricsInput::ParquetDirectory(directory)
    } else if let Some(database) = database_argument {
        MetricsInput::Database(database)
    } else if let Some(directory) = env::var_os("FITBIT_PARQUET_DIR") {
        MetricsInput::ParquetDirectory(PathBuf::from(directory))
    } else if let Some(database) = env::var_os("FITBIT_DATABASE_FILE") {
        MetricsInput::Database(PathBuf::from(database))
    } else {
        MetricsInput::ParquetDirectory(PathBuf::from("db/parquet"))
    };
    Ok((input, MetricProfile { age, reference_sex }))
}

fn run() -> Result<()> {
    let (input, profile) = parse_arguments()?;
    let report = match input {
        MetricsInput::ParquetDirectory(directory) => analyze_parquet(&directory, profile)?,
        MetricsInput::Database(database) => analyze_database(&database, profile)?,
    };
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("fitbit-metrics: {error:#}");
            ExitCode::FAILURE
        }
    }
}
