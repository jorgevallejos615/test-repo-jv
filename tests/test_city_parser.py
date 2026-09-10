from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pandas as pd

#Import the functions to be tested from the src.pipeline module
from src.pipeline import (
    aggregate_daily_weather,
    export_hot_city_alerts_json,
    export_merged_weather_report,
    fetch_weather_for_all_cities,
    fetch_weather_for_city,
    merge_city_weather_metrics,
    normalize_city_name,
    parse_city_csv,
    parse_hourly_forecast_dataframe,
)

#Normalize city names by stripping whitespace, removing special characters, and capitalizing words
def test_normalize_city_name_handles_common_dirty_strings():
    assert normalize_city_name("  ***new york***  ") == "New York"
    assert normalize_city_name("london & paris") == "London & Paris"
    assert normalize_city_name("tokyo-city") == "Tokyo-City"
    assert normalize_city_name("") == ""
    assert normalize_city_name(None) == ""

#Parse a CSV file containing city data and ensure that the resulting list of dictionaries has the expected structure and values
def test_parse_city_csv_normalizes_dirty_names():
    csv_path = Path(__file__).resolve().parents[1] / "data" / "raw_cities_dirty.csv"

    rows = parse_city_csv(csv_path)

    assert len(rows) >= 15
    assert rows[0]["city"] == "New York"
    assert rows[1]["city"] == "London"
    assert rows[2]["city"] == "Tokyo"
    assert rows[3]["city"] == "Sao Paulo"
    assert rows[4]["city"] == "Paris"
    assert rows[5]["city"] == "Mumbai"
    assert rows[8]["city"] == "Mexico City"
    assert rows[9]["city"] == "Berlin"
    assert rows[10]["city"] == "Seoul"
    assert rows[11]["city"] == "Bangkok"
    assert rows[12]["city"] == "Rome"
    assert rows[13]["city"] == "Toronto"
    assert rows[14]["city"] == "Buenos Aires"

    assert rows[0]["latitude"] == 40.7128
    assert rows[0]["longitude"] == -74.006

#Fetch weather data for a specific city using the Open-Meteo API, ensuring that the correct URL and parameters are used and that the response is parsed correctly
def test_fetch_weather_for_city_calls_open_meteo():
    captured = {}

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse({
            "timezone": "America/New_York",
            "hourly": {
                "time": ["2026-09-03T00:00"],
                "temperature_2m": [22.4],
                "precipitation": [0.0],
            },
            "current": {
                "apparent_temperature": 21.7,
                "wind_speed_10m": 12.5,
            },
        })

    with patch("src.pipeline.httpx.get", side_effect=fake_get) as mocked_get:
        result = fetch_weather_for_city("New York", 40.7128, -74.006)

    assert mocked_get.call_count == 1
    assert captured["url"] == "https://api.open-meteo.com/v1/forecast"
    assert captured["params"]["latitude"] == 40.7128
    assert captured["params"]["longitude"] == -74.006
    assert captured["params"]["hourly"] == "temperature_2m,precipitation"
    assert captured["params"]["timezone"] == "auto"
    assert result["city"] == "New York"
    assert result["temperature_c"] == 22.4
    assert result["precipitation_mm"] == 0.0
    assert result["wind_speed_kmh"] == 12.5

#Fetch weather data for all cities in the dataset, ensuring that the function runs sequentially and returns the expected results for each city
def test_fetch_weather_for_all_cities_runs_sequentially():
    calls = []
    all_cities = [
        {"city": "New York", "latitude": 40.7128, "longitude": -74.006},
        {"city": "London", "latitude": 51.5074, "longitude": -0.1278},
    ]

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, params=None, timeout=None):
        calls.append((params["latitude"], params["longitude"]))
        return FakeResponse({
            "timezone": "UTC",
            "hourly": {
                "time": ["2026-09-03T00:00"],
                "temperature_2m": [20.0],
                "precipitation": [1.5],
            },
            "current": {},
        })

    with patch("src.pipeline.httpx.get", side_effect=fake_get) as mocked_get:
        results = fetch_weather_for_all_cities(cities=all_cities)

    assert mocked_get.call_count == 2
    assert calls == [(40.7128, -74.006), (51.5074, -0.1278)]
    assert [item["city"] for item in results] == ["New York", "London"]
    assert results[0]["temperature_c"] == 20.0
    assert len(results) == 2

#Parse the hourly forecast payload into a DataFrame, ensuring that the resulting table has the expected columns, data types, and values
def test_parse_hourly_forecast_dataframe_builds_expected_table():
    payload = {
        "timezone": "America/New_York",
        "hourly": {
            "time": ["2026-09-03T00:00", "2026-09-03T01:00"],
            "temperature_2m": [21.5, 20.8],
            "precipitation": [0.0, 0.2],
        },
    }

    frame = parse_hourly_forecast_dataframe(payload, "New York")

    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == ["city", "timezone", "time", "temperature_2m", "precipitation"]
    assert list(frame["city"]) == ["New York", "New York"]
    assert frame["temperature_2m"].tolist() == [21.5, 20.8]
    assert frame["precipitation"].tolist() == [0.0, 0.2]
    assert pd.api.types.is_datetime64_any_dtype(frame["time"])

#Parse the hourly forecast payload into a DataFrame, ensuring that missing values are handled correctly and represented as NaN in the resulting table
def test_parse_hourly_forecast_dataframe_handles_missing_values():
    payload = {
        "timezone": "Europe/London",
        "hourly": {
            "time": ["2026-09-03T00:00", "2026-09-03T01:00"],
            "temperature_2m": [None, 18.0],
            "precipitation": [0.5, None],
        },
    }

    frame = parse_hourly_forecast_dataframe(payload, "London")

    assert pd.api.types.is_datetime64_any_dtype(frame["time"])
    assert pd.isna(frame["temperature_2m"].iloc[0])
    assert pd.isna(frame["precipitation"].iloc[1])

#Aggregate the hourly weather data into daily summaries, ensuring that the maximum temperature and total precipitation are calculated correctly for each city and day
def test_aggregate_daily_weather_summarizes_city_day_metrics():
    frame = pd.DataFrame([
        {"city": "New York", "time": pd.Timestamp("2026-09-03 00:00:00"), "temperature_2m": 21.5, "precipitation": 0.2},
        {"city": "New York", "time": pd.Timestamp("2026-09-03 01:00:00"), "temperature_2m": 23.0, "precipitation": 0.8},
        {"city": "New York", "time": pd.Timestamp("2026-09-04 00:00:00"), "temperature_2m": 19.5, "precipitation": 1.1},
        {"city": "London", "time": pd.Timestamp("2026-09-03 00:00:00"), "temperature_2m": 16.0, "precipitation": 2.0},
    ])

    summary = aggregate_daily_weather(frame)

    assert list(summary.columns) == ["city", "day", "max_temperature_c", "total_precipitation_mm"]
    assert summary["city"].tolist() == ["London", "New York", "New York"]
    assert summary["max_temperature_c"].tolist() == [16.0, 23.0, 19.5]
    assert summary["total_precipitation_mm"].tolist() == [2.0, 1.0, 1.1]

#Aggregate the hourly weather data into daily summaries, ensuring that missing values are handled correctly and that the output is sorted by city and day
def test_aggregate_daily_weather_handles_missing_values_and_sorts_output():
    frame = pd.DataFrame([
        {"city": "Paris", "time": pd.Timestamp("2026-09-05 10:00:00"), "temperature_2m": None, "precipitation": 0.6},
        {"city": "Paris", "time": pd.Timestamp("2026-09-05 11:00:00"), "temperature_2m": 27.5, "precipitation": None},
        {"city": "Berlin", "time": pd.Timestamp("2026-09-05 09:00:00"), "temperature_2m": 19.0, "precipitation": 0.3},
        {"city": "Berlin", "time": pd.Timestamp("2026-09-05 10:00:00"), "temperature_2m": 21.0, "precipitation": 0.4},
    ])

    summary = aggregate_daily_weather(frame)

    assert summary["city"].tolist() == ["Berlin", "Paris"]
    assert summary["day"].tolist() == ["2026-09-05", "2026-09-05"]
    assert summary["max_temperature_c"].tolist() == [21.0, 27.5]
    assert summary["total_precipitation_mm"].tolist() == [0.7, 0.6]

#Merge city metadata with daily weather summaries, ensuring that the resulting DataFrame contains all expected columns and rows
def test_merge_city_weather_metrics_maps_city_metadata_to_daily_summary():
    city_frame = pd.DataFrame([
        {"city": "New York", "latitude": 40.7128, "longitude": -74.0060},
        {"city": "London", "latitude": 51.5074, "longitude": -0.1278},
    ])
    weather_summary = pd.DataFrame([
        {"city": "New York", "day": "2026-09-03", "max_temperature_c": 23.0, "total_precipitation_mm": 1.0},
        {"city": "London", "day": "2026-09-03", "max_temperature_c": 16.0, "total_precipitation_mm": 2.0},
    ])

    merged = merge_city_weather_metrics(city_frame, weather_summary)

    assert list(merged.columns) == ["city", "latitude", "longitude", "day", "max_temperature_c", "total_precipitation_mm"]
    assert merged["city"].tolist() == ["London", "New York"]
    assert merged["latitude"].tolist() == [51.5074, 40.7128]
    assert merged["longitude"].tolist() == [-0.1278, -74.006]
    assert merged["max_temperature_c"].tolist() == [16.0, 23.0]

#Export the merged weather report to an Excel file, ensuring that the file is created successfully and that the contents match the expected structure and values
def test_export_merged_weather_report_creates_xlsx_file(tmp_path):
    merged = pd.DataFrame([
        {
            "city": "New York",
            "latitude": 40.7128,
            "longitude": -74.006,
            "day": "2026-09-03",
            "max_temperature_c": 23.0,
            "total_precipitation_mm": 1.2,
        },
        {
            "city": "London",
            "latitude": 51.5074,
            "longitude": -0.1278,
            "day": "2026-09-03",
            "max_temperature_c": 16.0,
            "total_precipitation_mm": 2.0,
        },
    ])

    report_path = tmp_path / "reports" / "weather_report.xlsx"
    result_path = export_merged_weather_report(merged, report_path)

    assert result_path == report_path
    assert result_path.exists()
    assert result_path.suffix == ".xlsx"

    reloaded = pd.read_excel(result_path)
    assert list(reloaded.columns) == [
        "city",
        "latitude",
        "longitude",
        "day",
        "max_temperature_c",
        "total_precipitation_mm",
    ]
    assert len(reloaded) == 2

#Export a simplified alert payload listing cities whose daily max temperature exceeds a threshold, ensuring that only the relevant cities are included in the output JSON file
def test_export_hot_city_alerts_json_creates_simplified_payload(tmp_path):
    merged = pd.DataFrame([
        {
            "city": "New York",
            "latitude": 40.7128,
            "longitude": -74.006,
            "day": "2026-09-03",
            "max_temperature_c": 32.4,
            "total_precipitation_mm": 1.2,
        },
        {
            "city": "London",
            "latitude": 51.5074,
            "longitude": -0.1278,
            "day": "2026-09-03",
            "max_temperature_c": 28.0,
            "total_precipitation_mm": 2.0,
        },
    ])

    alert_path = tmp_path / "reports" / "weather_alerts.json"
    result_path = export_hot_city_alerts_json(merged, alert_path, threshold_c=30)

    assert result_path == alert_path
    assert result_path.exists()
    assert result_path.suffix == ".json"

    payload = result_path.read_text(encoding="utf-8")
    assert "New York" in payload
    assert "32.4" in payload
    assert "London" not in payload
