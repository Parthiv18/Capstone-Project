from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path

router = APIRouter()


class HouseVariables(BaseModel):
    home_size: str
    insulation_quality: str
    hvac_type: str
    occupancy_start: str
    occupancy_end: str


@router.post("/house_variables")
def save_house_variables(vars: HouseVariables):
    """Save the submitted house variables to a text file in the backend folder.

    The file will be written next to the backend package as `house_variables.txt`.
    """
    try:
        # backend/api -> parent = backend, write file next to backend folder
        out_path = Path(__file__).resolve().parent / "data-files" / "house_variables.txt"
        lines = [f"{k}: {v}" for k, v in vars.dict().items()]
        content = "\n".join(lines) + "\n"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "ok", "file": str(out_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
