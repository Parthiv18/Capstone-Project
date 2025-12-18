from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Any, Dict, Union
from database import db
from datetime import datetime, timezone
import zoneinfo
import os

# fetch weather function imported lazily where needed to avoid circular imports

router = APIRouter()


# --- Request models ---
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


# --- Auth endpoints ---
@router.post("/signup")
def signup(data: SignupModel):
    if not data.username or not data.password:
        raise HTTPException(status_code=400, detail="username and password required")
    if not data.address or not data.address.strip():
        raise HTTPException(status_code=400, detail="address required for signup")
    ok = db.create_user(data.username, data.password, data.address.strip())
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

    address = db.get_user_address(data.username)

    # Refresh user's weather once per day (UTC -> local conversion)
    try:
        today_str = datetime.now(timezone.utc).astimezone(zoneinfo.ZoneInfo("America/Toronto")).date().isoformat()
        last_weather_date = db.get_user_weather_date(data.username)

        if last_weather_date != today_str and address:
            key = os.environ.get("GEOAPIFY_KEY")
            if key:
                # resolve address -> coords then fetch weather
                import requests
                from api.user_data_collection.get_weather_data_api import fetch_and_export_weather

                params = {"text": address, "format": "json", "apiKey": key}
                r = requests.get("https://api.geoapify.com/v1/geocode/search", params=params, timeout=10)
                if r.status_code == 200:
                    data_geo = r.json()
                    first = data_geo.get("results", [])[:1]
                    if first:
                        res = first[0]
                        lat = res.get("lat")
                        lon = res.get("lon")
                        if lat is not None and lon is not None:
                            weather_data = fetch_and_export_weather(lat, lon, days_ahead=7)
                            db.set_user_weather_with_date(data.username, weather_data, today_str)
    except Exception as e:
        # Log but don't fail login
        print(f"Warning: failed to refresh weather for {data.username}: {e}")

    return {"ok": True, "username": data.username, "address": address}


# --- User data endpoints (weather & house) ---
@router.post("/user/weather")
def save_user_weather(req: WeatherDataRequest):
    if not req.username:
        raise HTTPException(status_code=400, detail="username required")
    ok = db.set_user_weather(req.username, req.data)
    if not ok:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True}


@router.get("/user/weather")
def get_user_weather(username: str):
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    data = db.get_user_weather(username)
    if data is None:
        raise HTTPException(status_code=404, detail="no saved weather for user")
    return {"data": data}


@router.post("/user/house")
def save_user_house(req: HouseDataRequest):
    if not req.username:
        raise HTTPException(status_code=400, detail="username required")
    ok = db.set_user_house(req.username, req.data)
    if not ok:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True}


@router.get("/user/house")
def get_user_house(username: str):
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    data = db.get_user_house(username)
    if data is None:
        raise HTTPException(status_code=404, detail="no saved house variables for user")
    return {"data": data}
