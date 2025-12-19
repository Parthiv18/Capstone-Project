"""
Authentication API Routes
Handles user signup, login, and user data management.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, Union
from datetime import datetime, timezone
import zoneinfo
import os

from database import db

# ============================================================
# Constants
# ============================================================

TIMEZONE = "America/Toronto"
GEOAPIFY_URL = "https://api.geoapify.com/v1/geocode/search"

# ============================================================
# Request Models
# ============================================================

class SignupModel(BaseModel):
    username: str
    password: str
    address: str


class LoginModel(BaseModel):
    username: str
    password: str


class WeatherDataRequest(BaseModel):
    username: str
    data: Union[Dict[str, Any], str]


class HouseDataRequest(BaseModel):
    username: str
    data: Union[Dict[str, Any], str]


router = APIRouter()

# ============================================================
# Helper Functions
# ============================================================

def _get_today_date() -> str:
    """Get today's date in ISO format for the configured timezone."""
    return datetime.now(timezone.utc).astimezone(
        zoneinfo.ZoneInfo(TIMEZONE)
    ).date().isoformat()


def _refresh_user_weather(username: str, address: str) -> None:
    """Refresh user's weather data if it's outdated."""
    import requests
    from api.user_data_collection.weather_api import fetch_and_export_weather

    key = os.environ.get("GEOAPIFY_KEY")
    if not key or not address:
        return

    try:
        # Geocode the address
        params = {"text": address, "format": "json", "apiKey": key}
        response = requests.get(GEOAPIFY_URL, params=params, timeout=10)
        
        if response.status_code != 200:
            return

        results = response.json().get("results", [])
        if not results:
            return

        first_result = results[0]
        lat, lon = first_result.get("lat"), first_result.get("lon")
        
        if lat is None or lon is None:
            return

        # Fetch and save weather data
        weather_data = fetch_and_export_weather(lat, lon, days_ahead=7)
        db.set_user_weather_with_date(username, weather_data, _get_today_date())

    except Exception as e:
        print(f"Warning: Failed to refresh weather for {username}: {e}")


# ============================================================
# Auth Endpoints
# ============================================================

@router.post("/signup")
def signup(data: SignupModel):
    """Create a new user account."""
    if not data.username or not data.password:
        raise HTTPException(status_code=400, detail="Username and password required")
    
    if not data.address or not data.address.strip():
        raise HTTPException(status_code=400, detail="Address required for signup")
    
    success = db.create_user(data.username, data.password, data.address.strip())
    if not success:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    return {"ok": True}


@router.post("/login")
def login(data: LoginModel):
    """Authenticate a user."""
    if not data.username or not data.password:
        raise HTTPException(status_code=400, detail="Username and password required")
    
    if not db.verify_user(data.username, data.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    address = db.get_user_address(data.username)

    # Refresh weather data once per day
    try:
        today = _get_today_date()
        last_weather_date = db.get_user_weather_date(data.username)
        
        if last_weather_date != today and address:
            _refresh_user_weather(data.username, address)
    except Exception as e:
        print(f"Warning: Weather refresh failed for {data.username}: {e}")

    return {"ok": True, "username": data.username, "address": address}


# ============================================================
# User Data Endpoints
# ============================================================

@router.post("/user/weather")
def save_user_weather(req: WeatherDataRequest):
    """Save weather data for a user."""
    if not req.username:
        raise HTTPException(status_code=400, detail="Username required")
    
    if not db.set_user_weather(req.username, req.data):
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"ok": True}


@router.get("/user/weather")
def get_user_weather(username: str):
    """Get stored weather data for a user."""
    if not username:
        raise HTTPException(status_code=400, detail="Username required")
    
    data = db.get_user_weather(username)
    if data is None:
        raise HTTPException(status_code=404, detail="No saved weather for user")
    
    return {"data": data}


@router.post("/user/house")
def save_user_house(req: HouseDataRequest):
    """Save house variables for a user."""
    if not req.username:
        raise HTTPException(status_code=400, detail="Username required")
    
    if not db.set_user_house(req.username, req.data):
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"ok": True}


@router.get("/user/house")
def get_user_house(username: str):
    """Get stored house variables for a user."""
    if not username:
        raise HTTPException(status_code=400, detail="Username required")
    
    data = db.get_user_house(username)
    if data is None:
        raise HTTPException(status_code=404, detail="No saved house variables for user")
    
    return {"data": data}
