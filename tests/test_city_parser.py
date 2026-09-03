from pathlib import Path

import httpx
import pandas as pd

from src.pipeline import (
    aggregate_daily_weather,
    fetch_weather_for_all_cities,
    fetch_weather_for_city,
    merge_city_weather_metrics,
    parse_city_csv,
    parse_hourly_forecast_dataframe,
)


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


def test_fetch_weather_for_city_calls_open_meteo(monkeypatch):
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

    monkeypatch.setattr(httpx, "get", fake_get)

    result = fetch_weather_for_city("New York", 40.7128, -74.006)

    assert captured["url"] == "https://api.open-meteo.com/v1/forecast"
    assert captured["params"]["latitude"] == 40.7128
    assert captured["params"]["longitude"] == -74.006
    assert captured["params"]["hourly"] == "temperature_2m,precipitation"
    assert captured["params"]["timezone"] == "auto"
    assert result["city"] == "New York"
    assert result["temperature_c"] == 22.4
    assert result["precipitation_mm"] == 0.0
    assert result["wind_speed_kmh"] == 12.5


def test_fetch_weather_for_all_cities_runs_sequentially(monkeypatch):
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

    monkeypatch.setattr(httpx, "get", fake_get)

    results = fetch_weather_for_all_cities(cities=all_cities)

    assert calls == [(40.7128, -74.006), (51.5074, -0.1278)]
    assert [item["city"] for item in results] == ["New York", "London"]
    assert results[0]["temperature_c"] == 20.0
    assert len(results) == 2


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
