# Python Weather App

A small Python pipeline that cleans a dirty city dataset, queries the Open-Meteo API for hourly weather data, aggregates daily city weather metrics, merges the results back to the source city metadata, and exports final reports to the `reports/` folder.

## Requirements

- Python 3.14+
- `uv` for dependency and environment management

## Setup

From the project root, create and sync the environment with:

```bash
uv sync
```

This installs the dependencies declared in `pyproject.toml` and prepares the local environment for running the project.

## Run the pipeline

Execute the full pipeline with:

```bash
uv run python src/pipeline.py
```

This script will:

1. read the raw city CSV from `data/raw_cities_dirty.csv`
2. normalize the city names and coordinates
3. fetch weather data for each city from the Open-Meteo API
4. convert hourly payloads into pandas DataFrames
5. aggregate daily maximum temperatures and total precipitation per city
6. merge the weather metrics back to city metadata
7. export the final results to Excel and JSON files under `reports/`

## Output files

After a successful run, the project produces:

- `reports/weather_report.xlsx` — full merged daily city weather summary
- `reports/weather_alerts.json` — simplified alert payload for cities over the 30°C threshold
- `pipeline.log` — runtime logging output

## Example

```bash
uv run python src/pipeline.py
```

The console should print a summary indicating how many cities were processed, and the reports folder will contain the generated exports.

## Tests

Run the test suite with:

```bash
uv run pytest -q
```

This ensures the normalization, DataFrame transformations, API mocking, and reporting logic continue to work as expected.
