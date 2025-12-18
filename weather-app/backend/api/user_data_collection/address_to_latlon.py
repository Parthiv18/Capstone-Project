from fastapi import APIRouter, HTTPException
import os
import requests

router = APIRouter()


@router.get("/geocode")
def geocode(address: str):
    """Resolve a free-text address to { lat, lon } using Geoapify. The GEOAPIFY_KEY must be set in env."""
    key = os.environ.get("GEOAPIFY_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="Geoapify key not configured on server")

    if not address:
        raise HTTPException(status_code=400, detail="address query param required")

    params = {"text": address, "format": "json", "apiKey": key}
    try:
        r = requests.get("https://api.geoapify.com/v1/geocode/search", params=params, timeout=10)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"geocoding service returned {r.status_code}")

    data = r.json()
    first = data.get("results", [])[:1]
    if not first:
        raise HTTPException(status_code=404, detail="no results for address")

    res = first[0]
    lat = res.get("lat")
    lon = res.get("lon")
    if lat is None or lon is None:
        raise HTTPException(status_code=502, detail="geocoding returned no coordinates")

    return {"lat": lat, "lon": lon, "formatted": res.get("formatted")}


@router.get("/weather_address")
def weather_by_address(address: str, days_ahead: int = 7):
    """Geocode the address (text), then fetch and return weather rows (same shape as /weather).
    Returns the same dict as fetch_and_export_weather.
    """
    if not address:
        raise HTTPException(status_code=400, detail="address query param required")

    key = os.environ.get("GEOAPIFY_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="Geoapify key not configured on server")

    params = {"text": address, "format": "json", "apiKey": key}
    try:
        r = requests.get("https://api.geoapify.com/v1/geocode/search", params=params, timeout=10)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"geocoding service returned {r.status_code}")

    data = r.json()
    first = data.get("results", [])[:1]
    if not first:
        raise HTTPException(status_code=404, detail="no results for address")

    res = first[0]
    lat = res.get("lat")
    lon = res.get("lon")
    if lat is None or lon is None:
        raise HTTPException(status_code=502, detail="geocoding returned no coordinates")

    # Import here to avoid circular imports at module import time
    from api.user_data_collection.weather_api import fetch_and_export_weather

    try:
        weather = fetch_and_export_weather(lat, lon, days_ahead=days_ahead)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"failed to fetch weather: {e}")

    # attach resolved location info
    weather["address_formatted"] = res.get("formatted")
    weather["address"] = address
    weather["lat"] = lat
    weather["lon"] = lon
    return weather
