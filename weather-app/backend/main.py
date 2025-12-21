"""
Weather App Backend Server
FastAPI application with CORS support for the weather simulation app.
"""

import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import routers
from api.user_data_collection.house_api import router as house_router
from api.user_data_collection.address_to_latlon import router as geocode_router
from api.user_data_collection.weather_api import router as weather_router
from api.authentication.auth_api import router as auth_router
from api.alerts_simulation.alerts import router as alerts_router
from api.hvac_simulation.indoor_temp_simulation import (
    run_simulation_step,
    run_hvac_ai,
    run_simulation_step_with_hvac
)

# ============================================================
# Configuration
# ============================================================

load_dotenv()

ALLOWED_ORIGINS = ["http://localhost:5173"]

# ============================================================
# App Setup
# ============================================================

app = FastAPI(
    title="Weather App API",
    description="Backend API for weather simulation and HVAC management",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Routes
# ============================================================

@app.get("/api/simulation/{username}")
def get_simulation_step(username: str):
    """Run one simulation step for the given user."""
    try:
        result = run_simulation_step_with_hvac(username)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"Simulation Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hvac/{username}")
def get_hvac_schedule(username: str, target_temp: float = None):
    """
    Generate and return the HVAC AI schedule for the given user.
    
    Query params:
        target_temp: Desired temperature setpoint in Celsius (optional - uses saved value if not provided)
    """
    try:
        result = run_hvac_ai(username, target_temp_c=target_temp)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"HVAC AI Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/hvac/{username}/refresh")
def refresh_hvac_schedule(username: str, target_temp: float = None):
    """
    Force regenerate the HVAC schedule (useful after weather/house data changes).
    """
    try:
        result = run_hvac_ai(username, target_temp_c=target_temp)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"HVAC Refresh Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Register routers
app.include_router(house_router, tags=["House"])
app.include_router(geocode_router, tags=["Geocoding"])
app.include_router(weather_router, tags=["Weather"])
app.include_router(auth_router, tags=["Authentication"])
app.include_router(alerts_router, prefix="/api", tags=["Alerts"])

# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    import uvicorn
    
    try:
        uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
    except Exception as e:
        print(f"Error starting server: {e}")
        print("Ensure uvicorn is installed: pip install uvicorn")