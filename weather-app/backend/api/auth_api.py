from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import db
from datetime import datetime, timezone
import zoneinfo
from api.weather_api import fetch_and_export_weather

router = APIRouter()


class SignupModel(BaseModel):
    username: str
    password: str
    postalcode: str


class LoginModel(BaseModel):
    username: str
    password: str


@router.post("/signup")
def signup(data: SignupModel):
    if not data.username or not data.password:
        raise HTTPException(status_code=400, detail="username and password required")
    if not data.postalcode or not data.postalcode.strip():
        raise HTTPException(status_code=400, detail="postalcode required for signup")
    ok = db.create_user(data.username, data.password, data.postalcode.strip())
    if not ok:
        raise HTTPException(status_code=400, detail="username already exists")
    return {"ok": True}


@router.post("/login")
def login(data: LoginModel):
    if not data.username or not data.password:
        raise HTTPException(status_code=400, detail="username and password required")
    ok = db.verify_user(data.username, data.password)
    if not ok:
        raise HTTPException(status_code=401, detail="invalid username or password")
    postal = db.get_user_postal(data.username)
    
    # Check if weather data needs to be refreshed (different day)
    today_str = datetime.now(timezone.utc).astimezone(zoneinfo.ZoneInfo("America/Toronto")).date().isoformat()
    last_weather_date = db.get_user_weather_date(data.username)
    
    if last_weather_date != today_str and postal:
        # Weather data is stale or missing, refresh it
        try:
            # Get coordinates from postal code
            import os
            key = os.environ.get("GEOAPIFY_KEY")
            if key:
                code = postal.replace(" ", "")
                url = f"https://api.geoapify.com/v1/geocode/search?postcode={code}&format=json&apiKey={key}"
                import requests
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    data_geo = r.json()
                    first = data_geo.get("results", [])[:1]
                    if first:
                        res = first[0]
                        lat = res.get("lat")
                        lon = res.get("lon")
                        if lat is not None and lon is not None:
                            # Fetch fresh weather data (structured dict) and store it
                            weather_data = fetch_and_export_weather(lat, lon, days_ahead=7)
                            # Store the structured weather snapshot with today's date
                            db.set_user_weather_with_date(
                                data.username,
                                weather_data,
                                today_str,
                            )
        except Exception as e:
            # Log error but don't fail login if weather refresh fails
            print(f"Warning: failed to refresh weather for {data.username}: {e}")
    
    return {"ok": True, "username": data.username, "postalcode": postal}
