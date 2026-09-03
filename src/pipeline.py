import csv
import logging
import os
import re
import time
from pathlib import Path

import httpx
import pandas as pd
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT_DIR / "pipeline.log"
DOTENV_PATH = ROOT_DIR / ".env"

logger = logging.getLogger("weather_pipeline")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
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
        token = word.lower()
        if token in {"a", "an", "the", "and", "of"} and len(normalized_words) > 0:
            normalized_words.append(token)
        else:
            normalized_words.append(token.capitalize())

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
                except (TypeError, ValueError):
                    logger.error("Skipping invalid coordinate row for city: %s", row)
                    continue

                rows.append({
                    "city": city_name,
                    "latitude": latitude,
                    "longitude": longitude,
                })
                logger.info("Parsed city row: %s", city_name)
    except OSError as exc:
        logger.exception("Could not read CSV file: %s", csv_file)
        raise exc

    logger.info("Finished CSV parse. %s cities loaded.", len(rows))
    return rows


def parse_hourly_forecast_dataframe(payload: dict, city: str) -> pd.DataFrame:
    """Convert an Open-Meteo hourly forecast payload into a pandas DataFrame."""
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    temperatures = hourly.get("temperature_2m", [])
    precipitation = hourly.get("precipitation", [])

    if not times:
        return pd.DataFrame(columns=["city", "timezone", "time", "temperature_2m", "precipitation"])

    max_len = max(len(times), len(temperatures), len(precipitation))
    records = []

    for i in range(max_len):
        records.append({
            "city": city,
            "timezone": payload.get("timezone", "unknown"),
            "time": times[i] if i < len(times) else None,
            "temperature_2m": temperatures[i] if i < len(temperatures) else None,
            "precipitation": precipitation[i] if i < len(precipitation) else None,
        })

    dataframe = pd.DataFrame(records, columns=["city", "timezone", "time", "temperature_2m", "precipitation"])
    dataframe["temperature_2m"] = pd.to_numeric(dataframe["temperature_2m"], errors="coerce")
    dataframe["precipitation"] = pd.to_numeric(dataframe["precipitation"], errors="coerce")
    return dataframe


def fetch_weather_for_city(city: str, latitude: float, longitude: float) -> dict[str, float | str]:
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
    temperature = float(temperatures[0]) if temperatures else float(current.get("temperature_2m", 0.0))
    precipitation_mm = float(precipitation[0]) if precipitation else float(current.get("precipitation", 0.0))
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


def fetch_weather_for_all_cities(
    cities: list[dict[str, float | str]] | None = None,
    csv_path: str | Path | None = None,
) -> list[dict[str, float | str]]:
    """Sequentially request weather for each city in the dataset and log the total runtime."""
    refresh_logger_level()

    if cities is None:
        csv_file = Path(csv_path) if csv_path else ROOT_DIR / "data" / "raw_cities_dirty.csv"
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
    logger.info("Total execution time: %.2f seconds for %s cities.", elapsed, len(cities))
    return results


if __name__ == "__main__":
    example_path = ROOT_DIR / "data" / "raw_cities_dirty.csv"
    try:
        weather_results = fetch_weather_for_all_cities(csv_path=example_path)
        print(f"Fetched weather for {len(weather_results)} cities.")
    except FileNotFoundError:
        logger.error("Execution failed because the input CSV was missing.")
