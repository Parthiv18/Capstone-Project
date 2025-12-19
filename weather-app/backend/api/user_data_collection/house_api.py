from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path
from typing import Optional, List
from database import db

router = APIRouter()


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


@router.post("/house_variables")
def save_house_variables(vars: HouseVariables):
    """Save submitted house variables into the user's DB record.

    This endpoint now requires a `username` to be present in the request body. The
    house variables are serialized to a small key: value text block and stored in
    the `user_house` column of the user's row. Writing to disk is no longer performed.
    """
    try:
        # Store house variables as a structured JSON object in the DB
        if not vars.username:
            raise HTTPException(status_code=400, detail="username required to save house variables")

        house_obj = {
            "data": vars.dict(exclude={"appliances", "username"}),
            "appliances": vars.appliances,
        }

        ok = db.set_user_house(vars.username, house_obj)
        if not ok:
            raise HTTPException(status_code=404, detail="user not found")

        # Initialize simulated indoor temp on first house submission so
        # `/api/simulation/{username}` can start immediately.
        user_id = db.get_user_id(vars.username)
        if user_id is not None and db.get_simulated_temp(user_id) is None:
            initial_temp = 70.0
            raw_weather = db.get_user_weather(vars.username)

            weather_rows = None
            if isinstance(raw_weather, dict):
                weather_rows = raw_weather.get("rows")
            elif isinstance(raw_weather, list):
                weather_rows = raw_weather

            if weather_rows and isinstance(weather_rows, list) and len(weather_rows) > 0:
                first_row = weather_rows[0]
                if isinstance(first_row, dict) and "temperature_2m" in first_row:
                    try:
                        initial_temp = float(first_row["temperature_2m"])
                    except Exception:
                        initial_temp = 70.0

            db.update_simulated_temp(user_id, initial_temp)
        return {"status": "ok", "saved": "db"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
