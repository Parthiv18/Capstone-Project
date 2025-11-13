from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path
from typing import Optional
from datetime import datetime
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

        house_obj = vars.dict()

        ok = db.set_user_house(vars.username, house_obj)
        if not ok:
            raise HTTPException(status_code=404, detail="user not found")
        return {"status": "ok", "saved": "db"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
