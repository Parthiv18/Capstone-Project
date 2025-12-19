"""
House Variables API Module
Handles storage and retrieval of house configuration data.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from database import db

# ============================================================
# Models
# ============================================================

class HouseVariables(BaseModel):
    home_size: int
    age_of_house: int
    insulation_quality: str
    hvac_type: str
    hvac_age: Optional[int] = None
    personal_comfort: int
    occupancy: str
    appliances: Optional[List[str]] = None
    username: Optional[str] = None


router = APIRouter()

# ============================================================
# Endpoints
# ============================================================

@router.post("/house_variables")
def save_house_variables(vars: HouseVariables):
    """
    Save house variables to the user's DB record.
    
    Requires `username` in the request body. House variables are stored
    as JSON in the `user_house` column of the user's row.
    """
    if not vars.username:
        raise HTTPException(status_code=400, detail="Username required to save house variables")

    try:
        # Build house object
        house_obj = {
            "data": vars.model_dump(exclude={"appliances", "username"}),
            "appliances": vars.appliances or [],
        }

        # Save to database
        if not db.set_user_house(vars.username, house_obj):
            raise HTTPException(status_code=404, detail="User not found")

        # Initialize simulated indoor temp if this is first house submission
        _initialize_simulation_temp(vars.username)

        return {"status": "ok", "saved": "db"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _initialize_simulation_temp(username: str) -> None:
    """
    Initialize simulated indoor temperature on first house submission.
    Uses the first weather row's temperature or a default value.
    """
    user_id = db.get_user_id(username)
    if user_id is None:
        return

    # Skip if already initialized
    if db.get_simulated_temp(user_id) is not None:
        return

    # Try to get initial temp from weather data
    initial_temp = 70.0  # Default
    raw_weather = db.get_user_weather(username)

    weather_rows = None
    if isinstance(raw_weather, dict):
        weather_rows = raw_weather.get("rows")
    elif isinstance(raw_weather, list):
        weather_rows = raw_weather

    if weather_rows and len(weather_rows) > 0:
        first_row = weather_rows[0]
        if isinstance(first_row, dict) and "temperature_2m" in first_row:
            try:
                initial_temp = float(first_row["temperature_2m"])
            except (ValueError, TypeError):
                pass

    db.update_simulated_temp(user_id, initial_temp)
