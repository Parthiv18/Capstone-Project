import pandas as pd
from datetime import datetime, timedelta, timezone
import zoneinfo
import openmeteo_requests
import requests_cache
from retry_requests import retry
from pathlib import Path

API_DIR = Path(__file__).resolve().parent


def fetch_and_export_weather(
    lat,
    lon,
    tz_name="America/Toronto",
    days_ahead: int = 7,
    output_txt: str | None = None,
):
    """
    Fetch hourly weather for the next `days_ahead` days (including today) and export to a text file.
    Returns a dict with:
      - rows: list of row dicts (date in '%Y-%m-%d %H:%M:%S %Z' and numeric values or None)
      - file: path to the exported text file
      - start_date, end_date: ISO date strings for the requested range (end_date exclusive)
    Notes:
      days_ahead is the number of days to include starting today. For example days_ahead=7 returns 7 days:
      today + next 6 days (i.e., today through today + 6).
    """
    if output_txt is None:
        output_txt = str(API_DIR / "data-files" / f"weather_{days_ahead}days.txt")

    # Setup Open-Meteo client with caching & retry
    cache_session = requests_cache.CachedSession(".cache", expire_after=3600)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    client = openmeteo_requests.Client(session=retry_session)

    # API request - include desired hourly variables
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join([
            "temperature_2m",
            "apparent_temperature",
            "dew_point_2m",
            "relativehumidity_2m",
            "shortwave_radiation",
            "precipitation",
            "rain",
            "snowfall",
            "windspeed_10m",
        ]),
        "timezone": tz_name,
    }

    responses = client.weather_api(url, params=params)
    response = responses[0]
    hourly = response.Hourly()
    vars = hourly.Variables

    # Extract variables in the same order as requested above
    temperature = vars(0).ValuesAsNumpy()
    apparent_temp = vars(1).ValuesAsNumpy()
    dew_point = vars(2).ValuesAsNumpy()
    humidity = vars(3).ValuesAsNumpy()
    solar_rad = vars(4).ValuesAsNumpy()
    precipitation = vars(5).ValuesAsNumpy()
    rain = vars(6).ValuesAsNumpy()
    snowfall = vars(7).ValuesAsNumpy()
    windspeed = vars(8).ValuesAsNumpy()

    # Build time index (UTC -> local tz)
    times_utc = pd.date_range(
        start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
        end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=hourly.Interval()),
        inclusive="left",
    )
    times_local = times_utc.tz_convert(tz_name)

    df = pd.DataFrame(
        {
            "date": times_local,
            "temperature_2m": temperature,
            "apparent_temperature": apparent_temp,
            "dew_point_2m": dew_point,
            "humidity_2m": humidity,
            "solar_radiation": solar_rad,
            "precipitation": precipitation,
            "rain": rain,
            "snowfall": snowfall,
            "windspeed_10m": windspeed,
        }
    )

    # Define range: today_start (local) through today_start + days_ahead (exclusive)
    now_local = datetime.now(timezone.utc).astimezone(zoneinfo.ZoneInfo(tz_name))
    today = now_local.date()
    tz = zoneinfo.ZoneInfo(tz_name)
    today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=tz)
    range_end = today_start + timedelta(days=days_ahead)  # exclusive

    range_df = df[(df["date"] >= today_start) & (df["date"] < range_end)].copy()

    # Write human-readable text file for the full range
    header = (
        f"Hourly weather from {today_start.date()} through {(range_end - timedelta(days=1)).date()} "
        f"(inclusive)  -- lat={lat}, lon={lon}, tz={tz_name}\n"
    )
    with open(output_txt, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(range_df.to_string(index=False))

    # Helper to convert values to JSON-friendly types
    def as_json_val(v):
        if pd.isna(v):
            return None
        try:
            return float(v)
        except Exception:
            # If it's already serializable (e.g., string) return as-is
            return v

    # Build rows for JSON
    rows = []
    for _, r in range_df.iterrows():
        rows.append(
            {
                "date": r["date"].strftime("%Y-%m-%d %H:%M:%S %Z"),
                "temperature_2m": as_json_val(r["temperature_2m"]),
                "apparent_temperature": as_json_val(r["apparent_temperature"]),
                "dew_point_2m": as_json_val(r["dew_point_2m"]),
                "humidity_2m": as_json_val(r["humidity_2m"]),
                "solar_radiation": as_json_val(r["solar_radiation"]),
                "precipitation": as_json_val(r["precipitation"]),
                "rain": as_json_val(r["rain"]),
                "snowfall": as_json_val(r["snowfall"]),
                "windspeed_10m": as_json_val(r["windspeed_10m"]),
            }
        )

    return {
        "rows": rows,
        "file": output_txt,
        "start_date": today_start.date().isoformat(),
        "end_date_exclusive": range_end.date().isoformat(),
    }


if __name__ == "__main__":
    # Example coordinates (Toronto)
    lat = 43.716964
    lon = -79.821611
    result = fetch_and_export_weather(lat, lon, tz_name="America/Toronto", days_ahead=7)
    print(f"Exported file: {result['file']}")
