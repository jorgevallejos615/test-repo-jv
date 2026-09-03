from pathlib import Path

import httpx
import pandas as pd

from src.pipeline import (
    fetch_weather_for_all_cities,
    fetch_weather_for_city,
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
    assert rows[11]["city"] == "Riyadh"
    assert rows[12]["city"] == "Berlin"
    assert rows[13]["city"] == "Johannesburg"
    assert rows[14]["city"] == "Seoul"
    assert rows[15]["city"] == "Bangkok"
    assert rows[16]["city"] == "Rome"
    assert rows[17]["city"] == "Toronto"

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
