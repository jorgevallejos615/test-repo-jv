import csv
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
import pandas as pd
from dotenv import load_dotenv
from openpyxl.styles import Alignment, Font, PatternFill

ROOT_DIR = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT_DIR / "pipeline.log"
DOTENV_PATH = ROOT_DIR / ".env"

logger = logging.getLogger("weather_pipeline")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(file_handler)


def refresh_logger_level() -> None:
    """Refresh the logger level from the .env file so runtime config stays dynamic."""
    load_dotenv(DOTENV_PATH)
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)


def normalize_city_name(raw_name: str) -> str:
    """Normalize city names from messy CSV input into a clean title-cased format."""
    if raw_name is None:
        return ""

    cleaned = str(raw_name).strip()
    cleaned = cleaned.replace("\ufeff", "")
    cleaned = re.sub(r"[^A-Za-z0-9\s&-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return ""

    words = [word.strip() for word in cleaned.split(" ") if word.strip()]
    normalized_words = []

    for word in words:
        if word in {"&"}:
            normalized_words.append("&")
            continue

        segments = word.split("-")
        normalized_segments = []

        for index, segment in enumerate(segments):
            token = segment.lower()
            if token in {"a", "an", "the", "and", "of"} and normalized_segments:
                normalized_segments.append(token)
            else:
                normalized_segments.append(token.capitalize())

            if index < len(segments) - 1:
                normalized_segments.append("-")

        normalized_words.append("".join(normalized_segments))

    normalized = " ".join(normalized_words)
    normalized = re.sub(r"\s+([\-&])\s+", r" \1 ", normalized)
    return normalized.strip()


def parse_city_csv(csv_path: str | Path) -> list[dict[str, float | str]]:
    """Read a CSV file and return cleaned city rows with numeric coordinates."""
    refresh_logger_level()
    csv_file = Path(csv_path)
    rows: list[dict[str, float | str]] = []

    logger.info("Starting CSV parse for %s", csv_file)

    if not csv_file.exists():
        logger.error("CSV file not found: %s", csv_file)
        raise FileNotFoundError(f"CSV file not found: {csv_file}")

    try:
        with csv_file.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                if not row:
                    continue

                city_name = normalize_city_name(row.get("city", ""))
                try:
                    latitude = float(row.get("latitude", "").strip())
                    longitude = float(row.get("longitude", "").strip())
                    # fmt: off
                except TypeError, ValueError:
                    # fmt: on
                    logger.error("Skipping invalid coordinate row for city: %s", row)
                    continue

                rows.append(
                    {
                        "city": city_name,
                        "latitude": latitude,
                        "longitude": longitude,
                    }
                )
                logger.info("Parsed city row: %s", city_name)
    except OSError as exc:
        logger.exception("Could not read CSV file: %s", csv_file)
        raise RuntimeError(f"Failed to read CSV file: {csv_file}") from exc

    logger.info("Finished CSV parse. %s cities loaded.", len(rows))
    return rows


def parse_hourly_forecast_dataframe(payload: dict, city: str) -> pd.DataFrame:
    """Convert an Open-Meteo hourly forecast payload into a pandas DataFrame."""
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    temperatures = hourly.get("temperature_2m", [])
    precipitation = hourly.get("precipitation", [])

    if not times:
        return pd.DataFrame(
            columns=["city", "timezone", "time", "temperature_2m", "precipitation"]
        )

    max_len = max(len(times), len(temperatures), len(precipitation))
    records = []

    for i in range(max_len):
        records.append(
            {
                "city": city,
                "timezone": payload.get("timezone", "unknown"),
                "time": times[i] if i < len(times) else pd.NaT,
                "temperature_2m": temperatures[i] if i < len(temperatures) else None,
                "precipitation": precipitation[i] if i < len(precipitation) else None,
            }
        )

    dataframe = pd.DataFrame(
        records, columns=["city", "timezone", "time", "temperature_2m", "precipitation"]
    )
    dataframe["time"] = pd.to_datetime(dataframe["time"], errors="coerce")
    dataframe["temperature_2m"] = pd.to_numeric(
        dataframe["temperature_2m"], errors="coerce"
    )
    dataframe["precipitation"] = pd.to_numeric(
        dataframe["precipitation"], errors="coerce"
    )

    for column in ["temperature_2m", "precipitation"]:
        dataframe[column] = dataframe[column].fillna(value=pd.NA)

    return dataframe


def fetch_weather_for_city(
    city: str, latitude: float, longitude: float
) -> dict[str, float | str]:
    """Fetch hourly weather data for a city from the Open-Meteo forecast API."""
    refresh_logger_level()
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m,precipitation",
        "timezone": "auto",
    }

    logger.info("Fetching weather for %s at %.4f, %.4f", city, latitude, longitude)

    try:
        response = httpx.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        logger.exception("Weather API request failed for %s", city)
        raise RuntimeError(f"Failed to fetch weather for {city}: {exc}") from exc

    hourly = payload.get("hourly", {})
    temperatures = hourly.get("temperature_2m", [])
    precipitation = hourly.get("precipitation", [])

    current = payload.get("current", {})
    temperature = (
        float(temperatures[0])
        if temperatures
        else float(current.get("temperature_2m", 0.0))
    )
    precipitation_mm = (
        float(precipitation[0])
        if precipitation
        else float(current.get("precipitation", 0.0))
    )
    feels_like = float(current.get("apparent_temperature", temperature))
    wind_speed = float(current.get("wind_speed_10m", 0.0))

    weather = {
        "city": city,
        "timezone": payload.get("timezone", "unknown"),
        "temperature_c": temperature,
        "feels_like_c": feels_like,
        "precipitation_mm": precipitation_mm,
        "wind_speed_kmh": wind_speed,
        "hourly_forecast": parse_hourly_forecast_dataframe(payload, city),
    }

    logger.info(
        "Weather retrieved for %s: %.2fC, feels like %.2fC, %.2f mm precipitation, wind %.2f km/h",
        city,
        weather["temperature_c"],
        weather["feels_like_c"],
        weather["precipitation_mm"],
        weather["wind_speed_kmh"],
    )
    return weather


def aggregate_daily_weather(forecast_frame: pd.DataFrame) -> pd.DataFrame:
    """Group hourly forecast data by city and day to calculate daily max temperature and total precipitation."""
    if forecast_frame.empty:
        return pd.DataFrame(
            columns=["city", "day", "max_temperature_c", "total_precipitation_mm"]
        )

    aggregated = forecast_frame.copy()
    aggregated["day"] = pd.to_datetime(aggregated["time"]).dt.floor("D")
    aggregated["temperature_2m"] = pd.to_numeric(
        aggregated["temperature_2m"], errors="coerce"
    )
    aggregated["precipitation"] = pd.to_numeric(
        aggregated["precipitation"], errors="coerce"
    )

    summary = (
        aggregated.groupby(["city", "day"], as_index=False)
        .agg(
            max_temperature_c=("temperature_2m", "max"),
            total_precipitation_mm=("precipitation", "sum"),
        )
        .sort_values(["city", "day"])
        .reset_index(drop=True)
    )
    summary["day"] = pd.to_datetime(summary["day"]).dt.strftime("%Y-%m-%d")
    return summary


def merge_city_weather_metrics(
    city_frame: pd.DataFrame,
    weather_summary: pd.DataFrame,
) -> pd.DataFrame:
    """Join normalized city metadata to daily weather summaries using the city name."""
    city_df = city_frame.copy()
    city_df = (
        city_df[["city", "latitude", "longitude"]]
        .drop_duplicates(subset=["city"])
        .copy()
    )

    merged = city_df.merge(weather_summary, on="city", how="inner")
    merged = merged.sort_values(["city", "day"]).reset_index(drop=True)
    return merged


def export_merged_weather_report(
    merged_frame: pd.DataFrame,
    output_path: str | Path | None = None,
) -> Path:
    """Export the final merged city weather data to a formatted Excel report in the reports folder."""
    refresh_logger_level()

    report_path = (
        Path(output_path)
        if output_path is not None
        else ROOT_DIR / "reports" / "weather_report.xlsx"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    export_data = merged_frame.copy()
    if export_data.empty:
        logger.warning(
            "No merged weather data to export; creating an empty workbook at %s",
            report_path,
        )

    if "day" in export_data.columns:
        export_data["day"] = pd.to_datetime(
            export_data["day"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        export_data.to_excel(writer, index=False, sheet_name="Weather Report")
        worksheet = writer.sheets["Weather Report"]
        header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
        header_font = Font(bold=True, color="FFFFFF")

        for cell in worksheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        for column_cells in worksheet.columns:
            column_letter = column_cells[0].column_letter
            max_length = max(
                len(str(cell.value)) if cell.value is not None else 0
                for cell in column_cells
            )
            worksheet.column_dimensions[column_letter].width = min(max_length + 2, 24)

    logger.info("Exported merged weather report to %s", report_path)
    return report_path


def export_hot_city_alerts_json(
    merged_frame: pd.DataFrame,
    output_path: str | Path | None = None,
    threshold_c: float = 30.0,
) -> Path:
    """Export a simplified alert payload listing cities whose daily max temperature exceeds a threshold."""
    refresh_logger_level()

    report_path = (
        Path(output_path)
        if output_path is not None
        else ROOT_DIR / "reports" / "weather_alerts.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    if merged_frame.empty:
        payload = {
            "alert_level": "info",
            "threshold_c": threshold_c,
            "cities": [],
        }
        report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("Wrote empty alert payload to %s", report_path)
        return report_path

    alert_rows = merged_frame.copy()
    if "max_temperature_c" not in alert_rows.columns:
        raise ValueError(
            "merged_frame must include max_temperature_c for alert filtering"
        )

    filtered = alert_rows.loc[
        pd.to_numeric(alert_rows["max_temperature_c"], errors="coerce") > threshold_c
    ].copy()

    cities_payload = []
    for _, row in filtered.iterrows():
        cities_payload.append(
            {
                "city": row.get("city", ""),
                "day": row.get("day", ""),
                "latitude": row.get("latitude"),
                "longitude": row.get("longitude"),
                "max_temperature_c": float(row.get("max_temperature_c", 0.0)),
            }
        )

    payload = {
        "alert_level": "heat_alert" if cities_payload else "normal",
        "threshold_c": threshold_c,
        "cities": cities_payload,
    }

    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(
        "Exported weather alert payload to %s with %s cities above %.1fC",
        report_path,
        len(cities_payload),
        threshold_c,
    )
    return report_path


def fetch_weather_for_all_cities(
    cities: list[dict[str, float | str]] | None = None,
    csv_path: str | Path | None = None,
) -> list[dict[str, float | str]]:
    """Sequentially request weather for each city in the dataset and log the total runtime."""
    refresh_logger_level()

    if cities is None:
        csv_file = (
            Path(csv_path) if csv_path else ROOT_DIR / "data" / "raw_cities_dirty.csv"
        )
        cities = parse_city_csv(csv_file)

    total_start = time.perf_counter()
    logger.info("Starting sequential weather fetch for %s cities.", len(cities))

    results: list[dict[str, float | str]] = []
    for index, row in enumerate(cities, start=1):
        city = str(row["city"])
        latitude = float(row["latitude"])
        longitude = float(row["longitude"])
        logger.info("Processing city %s/%s: %s", index, len(cities), city)

        try:
            result = fetch_weather_for_city(city, latitude, longitude)
            results.append(result)
        except RuntimeError as exc:
            logger.error("Failed to fetch weather for %s: %s", city, exc)

    elapsed = time.perf_counter() - total_start
    logger.info(
        "Total execution time: %.2f seconds for %s cities.", elapsed, len(cities)
    )
    return results


if __name__ == "__main__":
    example_path = ROOT_DIR / "data" / "raw_cities_dirty.csv"
    try:
        weather_results = fetch_weather_for_all_cities(csv_path=example_path)
        if weather_results:
            city_frame = pd.DataFrame(parse_city_csv(example_path))
            hourly_frames = [
                forecast["hourly_forecast"]
                for forecast in weather_results
                if isinstance(forecast.get("hourly_forecast"), pd.DataFrame)
            ]
            hourly_combined = (
                pd.concat(hourly_frames, ignore_index=True)
                if hourly_frames
                else pd.DataFrame()
            )
            daily_summary = aggregate_daily_weather(hourly_combined)
            final_report = merge_city_weather_metrics(city_frame, daily_summary)
            export_merged_weather_report(final_report)
            export_hot_city_alerts_json(final_report, threshold_c=30.0)
        print(f"Fetched weather for {len(weather_results)} cities.")
    except FileNotFoundError:
        logger.error("Execution failed because the input CSV was missing.")
