import sys
import os
from pathlib import Path
from datetime import datetime
import zoneinfo

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- API Router Imports ---
from api.user_data_collection.get_weather_data_api import fetch_and_export_weather
from api.user_data_collection.get_house_data_api import router as house_router
from api.user_data_collection.postalcode_to_latlon import router as geocode_router
from api.authentication.auth_api import router as auth_router
from api.authentication.get_auth_user_data_api import router as user_data_router
from api.hvac_simulation.indoor_temp_simulation import run_simulation_step

# --- App Configuration ---
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Coord(BaseModel):
    lat: float
    lon: float

@app.post("/weather")
def weather(coord: Coord):
    try:
        out = fetch_and_export_weather(coord.lat, coord.lon)
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/simulation/{username}")
def get_simulation_step(username: str):
    try:
        result = run_simulation_step(username)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except Exception as e:
        print(f"Simulation Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Router Registration ---
app.include_router(house_router)
app.include_router(geocode_router)
app.include_router(auth_router)
app.include_router(user_data_router) # Registered the new router

# --- Entry Point ---
if __name__ == "__main__":
    try:
        import uvicorn
        uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
    except Exception as e:
        print("Error starting server. Ensure uvicorn is installed.")
        print(e)