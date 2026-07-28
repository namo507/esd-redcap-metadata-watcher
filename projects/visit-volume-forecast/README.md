# 36-Month Visit-Volume Forecast

Forecasts monthly *36-Month* visit volume from the projections export, with model
backtesting and data-quality reconciliation. The unit of analysis is
participant → 36-Month visit event.

## Layout

```text
visit_volume_forecast.ipynb    Pipeline: load, reconcile, backtest, forecast
36m_projections_export.csv     Source export (multi-block CSV, read with header=None)
ml_forecast_outputs/           Generated tables, figures, and run log
```

## Run

Open `visit_volume_forecast.ipynb` and run it from this folder. The config cell sets
`input_csv = "36m_projections_export.csv"` and `output_dir = "ml_forecast_outputs"`, both
relative to the working directory.

The source CSV is a stacked multi-block export rather than a single rectangular table, so
it is read with `header=None, dtype=str` and split by the notebook before parsing.

## Outputs

`ml_forecast_outputs/` separates outputs by sensitivity:

- `SHAREABLE_*.csv` — data dictionary, discrepancy report, 36-month forecast summary,
  period totals, model ranking backtest, and monthly reconciliation. Safe to circulate.
- `RESTRICTED_*` — the participant/timepoint master CSV and the combined workbook. These
  are participant-level; do not circulate them outside the study team.
- `figures/` — nine numbered PNGs covering the monthly series, ACF/PACF, seasonality,
  recruitment signal, backtest errors, the 36-month forecast, model ranking, the next 90
  days, and a data-quality dashboard.
- `run_log.json` — parameters and timings for the last run.
