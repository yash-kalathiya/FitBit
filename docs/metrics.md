# Metric definitions

These metrics are local, transparent explorations. They are not medical advice,
diagnoses, WHOOP formulas, or replacements for Fitbit/Google metrics. Every
relationship is observational and can be distorted by missing wear time,
illness, travel, device changes, or small samples.

## Daily strain (0-21)

For each consecutive heart-rate sample no more than two minutes apart, the
engine calculates heart-rate reserve using daily resting heart rate and
`208 - 0.7 * age` as estimated maximum heart rate. The interval contributes:

```text
minutes * (heart-rate-reserve fraction ^ 2) * 10
```

Daily load units are mapped logarithmically onto 0-21:

```text
strain = clamp(21 * ln(1 + load_units / 10) / ln(101), 0, 21)
```

This sets 1,000 load units as the explicit top-of-scale reference instead of
allowing ordinary active days to hit the ceiling. It is a project calibration,
not a proprietary or clinically validated formula. Gaps over two minutes add no
load, so `observed_hours` must be considered with the score. This is a fresh
calendar-day calculation, not Fitbit's accumulating weekly target load.

## Fitness age estimate

The latest 30-day mean VO2 max is inverted through a simple adult population
reference equation:

```text
male reference:   VO2 max = -0.42 * age + 58
female reference: VO2 max = -0.35 * age + 46
```

The estimate is bounded to ages 18-90 and reports the selected reference and
observation count. It is deliberately simpler than proprietary healthspan
scores and has no clinical interpretation.

## Personal readiness and analog mornings

The physiology component compares today's RHR and HRV with their preceding
28-day medians. Sleep contributes 20% to the displayed readiness score. The
engine standardizes sleep duration, RHR, HRV, and prior-day strain, then finds
the five closest historical mornings. Their similarity-weighted completed
strain becomes an explainable capacity estimate.

At least 15 complete profiles are required. Similarity is descriptive; it does
not mean the user should reproduce an earlier day's activity.

## Sleep opportunity regression

A two-feature ridge regression fits next-morning physiology against previous-day
strain and that night's sleep duration. The engine solves the fitted model for
a target physiology score of 70/100, then bounds the result to the user's
observed 10th-90th percentile sleep range.

The metric appears only with at least 30 paired days and a positive fitted sleep
coefficient. Training-set R-squared and sample count stay visible. “Sleep
opportunity” is used instead of “sleep prescription”: the association does not
prove that a particular duration will cause recovery or erase strain.

## Dashboard signal comparison

Sleep duration, HRV, and resting heart rate use different physical units, so
the dashboard does not draw their raw values on one axis. Each series is shown
as percentage change from its preceding 28-day median. RHR is inverted so a
positive line always means the recovery-favorable direction: more sleep, higher
HRV, or lower RHR. Steps remain on a separate axis because their magnitude is
thousands rather than tens.

## Cross-history insights

- **Strain-to-next-sleep response:** next-night sleep after the highest quartile
  of strain days versus all other paired days, plus Pearson correlation.
- **Recovery lag:** median days after a high-strain day for both RHR and HRV to
  return near their preceding 28-day baselines.
- **Sleep regularity:** circular variability of the last 30 sleep midpoints,
  converted to an easy 0-100 score. Circular math handles times around midnight.
- **Recent-load ratio:** mean daily strain over the latest seven observed days
  divided by the preceding available 28-day mean.
