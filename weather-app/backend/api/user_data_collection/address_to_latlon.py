"""
Geocoding API Module
Resolves addresses to coordinates using Geoapify service.
"""

from fastapi import APIRouter, HTTPException
import os
import requests

# ============================================================
# Constants
# ============================================================

GEOAPIFY_URL = "https://api.geoapify.com/v1/geocode/search"
REQUEST_TIMEOUT = 10

router = APIRouter()

# ============================================================
# Helper Functions
# ============================================================

def _get_api_key() -> str:
    """Get Geoapify API key from environment."""
    key = os.environ.get("GEOAPIFY_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="Geoapify key not configured on server")
    return key


def _geocode_address(address: str) -> dict:
    """Resolve address to coordinates."""
    key = _get_api_key()
    
    params = {"text": address, "format": "json", "apiKey": key}
    
    try:
        response = requests.get(GEOAPIFY_URL, params=params, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Geocoding request failed: {e}")
    
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Geocoding service returned {response.status_code}")
    
    data = response.json()
    results = data.get("results", [])
    
    if not results:
        raise HTTPException(status_code=404, detail="No results for address")
    
    first_result = results[0]
    lat = first_result.get("lat")
    lon = first_result.get("lon")
    
    if lat is None or lon is None:
        raise HTTPException(status_code=502, detail="Geocoding returned no coordinates")
    
    return {
        "lat": lat,
        "lon": lon,
        "formatted": first_result.get("formatted"),
    }


# ============================================================
# Endpoints
# ============================================================

@router.get("/geocode")
def geocode(address: str):
    """Resolve a free-text address to coordinates using Geoapify."""
    if not address:
        raise HTTPException(status_code=400, detail="Address query param required")
    
    return _geocode_address(address)


@router.get("/weather_address")
def weather_by_address(address: str, days_ahead: int = 7):
    """
    Geocode the address, then fetch and return weather data.
    Returns the same structure as the /weather endpoint.
    """
    if not address:
        raise HTTPException(status_code=400, detail="Address query param required")
    
    # Geocode the address
    geo_result = _geocode_address(address)
    lat, lon = geo_result["lat"], geo_result["lon"]
    
    # Fetch weather data
    from api.user_data_collection.weather_api import fetch_and_export_weather
    
    try:
        weather = fetch_and_export_weather(lat, lon, days_ahead=days_ahead)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch weather: {e}")
    
    # Attach location info
    weather["address_formatted"] = geo_result["formatted"]
    weather["address"] = address
    weather["lat"] = lat
    weather["lon"] = lon
    
    return weather
