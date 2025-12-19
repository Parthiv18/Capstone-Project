"""
Weather API Module
Fetches weather data from Open-Meteo API and provides endpoints for weather data.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import zoneinfo

import pandas as pd
import openmeteo_requests
import requests_cache
from retry_requests import retry

# ============================================================
# Constants
# ============================================================

DEFAULT_TIMEZONE = "America/Toronto"
CACHE_EXPIRY_SECONDS = 3600
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_VARIABLES = [
    "temperature_2m",
    "apparent_temperature",
    "dew_point_2m",
    "relativehumidity_2m",
    "shortwave_radiation",
    "precipitation",
    "rain",
    "snowfall",
    "windspeed_10m",
]

# ============================================================
# Weather Fetching
# ============================================================

def fetch_and_export_weather(
    lat: float,
    lon: float,
    tz_name: str = DEFAULT_TIMEZONE,
    days_ahead: int = 7,
) -> dict:
    """
    Fetch hourly weather for the next `days_ahead` days (including today).
    
    Returns:
        Dict containing:
        - rows: list of hourly weather data
        - text: formatted text representation
        - lat, lon: coordinates
        - start_date, end_date_exclusive: date range
    """
    # Setup cached session with retry
    cache_session = requests_cache.CachedSession(".cache", expire_after=CACHE_EXPIRY_SECONDS)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    client = openmeteo_requests.Client(session=retry_session)

    # Fetch weather data
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_VARIABLES),
        "timezone": tz_name,
    }

    responses = client.weather_api(OPENMETEO_URL, params=params)
    response = responses[0]
    hourly = response.Hourly()
    variables = hourly.Variables

    # Extract variable arrays
    weather_vars = {
        "temperature_2m": variables(0).ValuesAsNumpy(),
        "apparent_temperature": variables(1).ValuesAsNumpy(),
        "dew_point_2m": variables(2).ValuesAsNumpy(),
        "humidity_2m": variables(3).ValuesAsNumpy(),
        "solar_radiation": variables(4).ValuesAsNumpy(),
        "precipitation": variables(5).ValuesAsNumpy(),
        "rain": variables(6).ValuesAsNumpy(),
        "snowfall": variables(7).ValuesAsNumpy(),
        "windspeed_10m": variables(8).ValuesAsNumpy(),
    }

    # Build time range
    times_utc = pd.date_range(
        start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
        end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=hourly.Interval()),
        inclusive="left",
    )
    times_local = times_utc.tz_convert(tz_name)

    # Create DataFrame
    df = pd.DataFrame({"date": times_local, **weather_vars})

    # Filter to requested date range
    tz = zoneinfo.ZoneInfo(tz_name)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    today = now_local.date()
    today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=tz)
    range_end = today_start + timedelta(days=days_ahead)

    range_df = df[(df["date"] >= today_start) & (df["date"] < range_end)].copy()

    # Build text representation
    header = (
        f"Hourly weather from {today_start.date()} through {(range_end - timedelta(days=1)).date()} "
        f"(inclusive)  -- lat={lat}, lon={lon}, tz={tz_name}\n"
    )
    text_content = header + range_df.to_string(index=False)

    # Convert to JSON-serializable rows
    rows = [_row_to_dict(row) for _, row in range_df.iterrows()]

    return {
        "rows": rows,
        "text": text_content,
        "lat": lat,
        "lon": lon,
        "start_date": today_start.date().isoformat(),
        "end_date_exclusive": range_end.date().isoformat(),
    }


def _row_to_dict(row: pd.Series) -> dict:
    """Convert a DataFrame row to a JSON-serializable dict."""
    def safe_float(v: Any) -> Optional[float]:
        if pd.isna(v):
            return None
        try:
            return float(v)
        except Exception:
            return v

    return {
        "date": row["date"].strftime("%Y-%m-%d %H:%M:%S %Z"),
        "temperature_2m": safe_float(row["temperature_2m"]),
        "apparent_temperature": safe_float(row["apparent_temperature"]),
        "dew_point_2m": safe_float(row["dew_point_2m"]),
        "humidity_2m": safe_float(row["humidity_2m"]),
        "solar_radiation": safe_float(row["solar_radiation"]),
        "precipitation": safe_float(row["precipitation"]),
        "rain": safe_float(row["rain"]),
        "snowfall": safe_float(row["snowfall"]),
        "windspeed_10m": safe_float(row["windspeed_10m"]),
    }


# ============================================================
# Router & Endpoints
# ============================================================

router = APIRouter()


class Coord(BaseModel):
    lat: float
    lon: float


@router.post("/weather")
def get_weather(coord: Coord):
    """Fetch weather data for given coordinates."""
    try:
        return fetch_and_export_weather(coord.lat, coord.lon)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
