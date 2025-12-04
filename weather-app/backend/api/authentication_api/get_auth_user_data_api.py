from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, Union
from database import db

router = APIRouter()

# --- Data Models ---

class WeatherDataRequest(BaseModel):
    username: str
    # Accepts either a raw string or a JSON object/dict
    data: Union[Dict[str, Any], str] 

class HouseDataRequest(BaseModel):
    username: str
    # Accepts either a raw string or a JSON object/dict
    data: Union[Dict[str, Any], str]

# --- Weather Endpoints ---

@router.post("/user/weather")
def save_user_weather(req: WeatherDataRequest):
    """
    Manually save weather data for a user. 
    (Note: Login also does this automatically)
    """
    if not req.username:
        raise HTTPException(status_code=400, detail="username required")
    
    # db.set_user_weather expects the data (usually serialized JSON or dict)
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

# --- House Endpoints ---

@router.post("/user/house")
def save_user_house(req: HouseDataRequest):
    if not req.username:
        raise HTTPException(status_code=400, detail="username required")

    # Store house variables
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
        # It's often better to return empty data than a 404 for house configs
        # depending on your frontend logic, but we'll stick to 404 to be safe.
        raise HTTPException(status_code=404, detail="no saved house variables for user")
    
    return {"data": data}