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
    """Save the submitted house variables to a text file in the backend/data-files folder.

    Each submission is appended as a small block with a timestamp so options are preserved.
    """
    try:
        out_path = Path(__file__).resolve().parent / "data-files" / "house_variables.txt"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # write a simple key: value list (overwrite) in a stable order
        order = [
            "home_size",
            "age_of_house",
            "insulation_quality",
            "hvac_type",
            "hvac_age",
            "personal_comfort",
            "occupancy",
        ]

        lines = []
        d = vars.dict()
        for k in order:
            v = d.get(k)
            if v is None:
                continue
            lines.append(f"{k}: {v}")

        content = "\n".join(lines) + "\n"

        if vars.username:
            # save into user's DB record
            ok = db.set_user_house(vars.username, content)
            if not ok:
                raise HTTPException(status_code=404, detail="user not found")
            return {"status": "ok", "saved": "db"}

        # fallback: write the global file (existing behaviour)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)

        return {"status": "ok", "file": str(out_path)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
