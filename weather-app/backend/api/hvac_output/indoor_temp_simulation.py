import json
import datetime
from database.db import get_user_house, get_user_weather, get_user_id, get_simulated_temp, update_simulated_temp


# --- Helper Functions ---

def get_insulation_coefficient(quality):
    """
    Maps qualitative insulation to a heat transfer coefficient (k).
    0.01 = excellent (very insulated)
    0.03 = average
    0.05 = poor (bad insulation)
    """
    mapping = {
        "poor": 0.05,
        "average": 0.03,
        "excellent": 0.01
    }
    return mapping.get(str(quality).lower(), 0.03)

def calculate_house_volume(home_size_sqft):
    """Estimates House Volume in cubic meters for the simulation."""
    # Convert sqft to m2 (1 sqft = 0.092903 m2)
    area_m2 = home_size_sqft * 0.092903
    # Assume 2.5m ceiling height (approx 8ft)
    volume = area_m2 * 2.5
    return volume

def simulate_indoor_temp(
    indoor_temp,
    outdoor_temp,
    windspeed,
    solar_radiation,
    precipitation,
    house_volume,
    insulation
):
    """
    Returns next hour indoor temperature based on the simplified physics model.
    """
    # 1) Base heat-transfer coefficient
    k = insulation

    # 2) Wind increases heat loss
    k *= (1 + windspeed / 50)

    # 3) Big houses change temperature slower
    # Using max(..., 1) to ensure we don't divide by zero or have huge multipliers for tiny houses
    k /= max(house_volume / 200, 1)

    # 4) Core indoor temp shift toward outdoor (Newton's Cooling Law)
    delta = (outdoor_temp - indoor_temp) * k

    # 5) Solar heat gain
    solar_gain = solar_radiation * 0.0002

    # 6) Precipitation cooling (rain/snow)
    precip_cooling = -0.1 if precipitation > 0 else 0

    # Final next-hour temp
    next_temp = indoor_temp + delta + solar_gain + precip_cooling

    return round(next_temp, 2)

def run_simulation_step(username: str):
    """
    Runs the physics simulation for one hour time step using the simplified logic.
    """
    user_id = get_user_id(username)
    if not user_id:
        return {"error": "User not found"}

    # 1. Fetch Static House Data
    house_data = get_user_house(username)
    if not house_data:
        house_data = {}
    
    # Extract House Parameters
    area_sqft = float(house_data.get("home_size", 1500))
    insulation_quality = house_data.get("insulation_quality", "average")
    
    # Prepare parameters for the formula
    house_volume = calculate_house_volume(area_sqft)
    insulation_k = get_insulation_coefficient(insulation_quality)

    # 2. Fetch Dynamic Weather Data
    raw_weather_data = get_user_weather(username)
    if not raw_weather_data:
        return {"error": "User weather data not found"}

    # Handle DB format (list or dict with "rows")
    weather_rows = []
    if isinstance(raw_weather_data, dict):
        weather_rows = raw_weather_data.get("rows", [])
    elif isinstance(raw_weather_data, list):
        weather_rows = raw_weather_data
    
    if not weather_rows:
        return {"error": "Weather data format is invalid or empty"}

    # Pick the current weather row (index 0 for current simulation step)
    weather_row = weather_rows[0]

    T_out = float(weather_row.get("temperature_2m", 0))
    wind_speed = float(weather_row.get("windspeed_10m", 0))
    solar_rad = float(weather_row.get("solar_radiation", 0))
    
    # Calculate total precipitation (rain + snow)
    rain = float(weather_row.get("rain", 0))
    snow = float(weather_row.get("snowfall", 0))
    precipitation = rain + snow

    # 3. Get Current Indoor State
    # If no history exists (first run), start at a standard comfortable temp (e.g. 21C)
    # or start at T_out if you prefer immediate equilibration.
    T_in = get_simulated_temp(user_id)
    if T_in is None:
        T_in = 21.0

    # 4. Calculate Next Temperature using the new simplified logic
    T_next = simulate_indoor_temp(
        indoor_temp=T_in,
        outdoor_temp=T_out,
        windspeed=wind_speed,
        solar_radiation=solar_rad,
        precipitation=precipitation,
        house_volume=house_volume,
        insulation=insulation_k
    )

    # 5. Update Database with the new simulated temperature
    update_simulated_temp(user_id, T_next)

    # 6. Return result structure required by frontend
    return {
        "timestamp": weather_row.get("date", "Unknown"),
        "T_in_prev": T_in,
        "T_in_new": T_next,
    }