from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from datetime import datetime, timedelta, timezone
from pathlib import Path
import zoneinfo
from api.weather_api import fetch_and_export_weather
from api.house_api import router as house_router
from api.geocode_api import router as geocode_router
from api.auth_api import router as auth_router
from api.user_api import router as user_router
from dotenv import load_dotenv
import os

# Load .env from backend folder for local development (no-op if not present)
load_dotenv()

app = FastAPI()

# Allow local frontend dev server
# letting the backend talk to the frontend, also allows cookies, auth headers, and HTTP methods
# in production, you would lock this down to your actual frontend domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# needed to collect the lat and lon from the post request
class Coord(BaseModel):
    lat: float
    lon: float




# fetch_and_export_weather moved to backend/get_weather.py

@app.post("/weather")
def weather(coord: Coord):
    try:
        out = fetch_and_export_weather(coord.lat, coord.lon)
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.include_router(house_router)
app.include_router(geocode_router)
app.include_router(auth_router)
app.include_router(user_router)


@app.get("/download")
def download(lat: float, lon: float):
    try:
        out = fetch_and_export_weather(lat, lon)
        with open(out["file"], "rb") as f:
            data = f.read()
        return Response(content=data, media_type="text/plain")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    # Simple entrypoint so you can run: python main.py
    try:
        import uvicorn
        # server needs to start reliably with reload, even if uvicorn is not installed as a package
        # start->import uvicorn->sucess run uvicorn or print error->try to run uvicorn from command line->success run app or print error
        try:
            uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
        except ModuleNotFoundError:
            print("Warning: reload unavailable because the import string failed; starting without reload.")
            uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
    except Exception as e:
        print("uvicorn not available or failed to start programmatically.")
        print("If you haven't installed dependencies, run:")
        print("  pip install -r requirements.txt")
        print("Then start with: uvicorn main:app --reload --port 8000 (from the backend folder)")
        print("Or install uvicorn into your venv: pip install uvicorn[standard]")
        raise
