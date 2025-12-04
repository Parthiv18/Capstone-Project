import json
import datetime
from database.db import get_user_house, get_user_weather
from database.user_thermostat_db import get_simulated_temp, update_simulated_temp

# --- Physics Constants ---
AIR_DENSITY = 1.225  # kg/m^3 (rho)
SPECIFIC_HEAT_AIR = 1005  # J/kg*K (Cp)
CEILING_HEIGHT = 2.5  # meters (approx 8ft)
WINDOW_RATIO = 0.15   # Windows are approx 15% of floor area
SHGC_AVG = 0.5        # Solar Heat Gain Coefficient

def get_insulation_u_value(quality):
    """Maps qualitative insulation to U-values (W/m^2K)."""
    mapping = {
        "poor": 1.2,
        "average": 0.5,
        "excellent": 0.15
    }
    return mapping.get(str(quality).lower(), 0.5)

def get_ach(quality, wind_speed):
    """Calculates Air Changes per Hour based on tightness and wind."""
    q_str = str(quality).lower()
    base_ach = 0.5 if q_str == "average" else (1.0 if q_str == "poor" else 0.1)
    Kw = 0.02 # Wind factor coefficient
    return base_ach + (Kw * wind_speed)

def calculate_thermal_mass(home_size_sqft):
    """Estimates House Thermal Mass (C_home)."""
    area_m2 = home_size_sqft * 0.092903 # Convert sqft to m2
    volume = area_m2 * CEILING_HEIGHT
    
    # Air Mass Thermal Capacity (J/C) = Volume * Density * Cp
    c_air = volume * AIR_DENSITY * SPECIFIC_HEAT_AIR
    
    # Structure Thermal Mass acts as a multiplier
    STRUCTURE_MULTIPLIER = 12 
    return c_air * STRUCTURE_MULTIPLIER

def run_simulation_step(username: str):
    """
    Runs the physics simulation for one hour time step.
    """
    # 1. Fetch Static House Data
    house_data = get_user_house(username)
    if not house_data:
        # Fallback if house data isn't set up yet
        house_data = {}
    
    area_sqft = float(house_data.get("home_size", 1500))
    insulation = house_data.get("insulation_quality", "average")
    target_temp = float(house_data.get("personal_comfort", 22))
    
    # Derived Physics Parameters
    area_m2 = area_sqft * 0.092903
    U_eff = get_insulation_u_value(insulation)
    C_home = calculate_thermal_mass(area_sqft)
    A_envelope = area_m2 * 2.5 
    A_windows = area_m2 * WINDOW_RATIO

    # 2. Fetch Dynamic Weather Data
    raw_weather_data = get_user_weather(username)
    if not raw_weather_data:
        return {"error": "User weather data not found"}

    # FIX: Handle the "rows" key correctly from the main DB response
    weather_rows = []
    if isinstance(raw_weather_data, dict):
        weather_rows = raw_weather_data.get("rows", [])
    elif isinstance(raw_weather_data, list):
        weather_rows = raw_weather_data
    
    if not weather_rows:
        return {"error": "Weather data format is invalid or empty"}

    # Pick the current weather row (index 0 for simulation demo)
    weather_row = weather_rows[0]

    T_out = float(weather_row.get("temperature_2m", 0))
    wind_speed = float(weather_row.get("windspeed_10m", 0))
    solar_rad = float(weather_row.get("solar_radiation", 0))
    is_raining = float(weather_row.get("rain", 0)) > 0
    is_snowing = float(weather_row.get("snowfall", 0)) > 0

    # 3. Get Current Indoor State
    T_in = get_simulated_temp(username)
    if T_in is None:
        T_in = target_temp # Start at target if no history

    # 4. Calculate Heat Gains/Losses (in Watts)
    
    # Adjust U_eff for snow
    current_U = U_eff
    if is_snowing:
        current_U = U_eff * 0.7
    
    # Adjust Boundary Temp for rain
    T_boundary = T_out
    if is_raining:
        dew_point = weather_row.get("dew_point_2m")
        if dew_point is not None:
            T_boundary = float(dew_point)
        else:
            T_boundary = T_out - 2 

    # Conductive Load
    Q_conductive = current_U * A_envelope * (T_boundary - T_in)

    # Wind Load
    ACH = get_ach(insulation, wind_speed)
    V_home = area_m2 * CEILING_HEIGHT
    Q_wind = AIR_DENSITY * SPECIFIC_HEAT_AIR * V_home * (ACH / 3600) * (T_out - T_in)

    # Solar Load
    Q_solar = A_windows * SHGC_AVG * solar_rad

    # HVAC Logic
    Q_hvac = 0
    HVAC_POWER_CAPACITY = 10000 # Watts
    deadband = 0.5
    
    mode = "off"
    if T_in < (target_temp - deadband):
        Q_hvac = HVAC_POWER_CAPACITY # Heat ON
        mode = "heating"
    elif T_in > (target_temp + deadband):
        Q_hvac = -HVAC_POWER_CAPACITY # Cool ON
        mode = "cooling"

    # 5. Calculate Next Temperature
    Q_total = Q_conductive + Q_wind + Q_solar + Q_hvac
    dt = 3600 # 1 hour step

    T_next = T_in + (Q_total * dt) / C_home

    # 6. Update Database
    update_simulated_temp(username, T_next)

    return {
        "timestamp": weather_row.get("date", "Unknown"),
        "T_in_prev": round(T_in, 2),
        "T_in_new": round(T_next, 2),
        "T_out": round(T_out, 2),
        "hvac_mode": mode,
        "Q_components": {
            "conductive": round(Q_conductive, 2),
            "wind": round(Q_wind, 2),
            "solar": round(Q_solar, 2),
            "hvac": Q_hvac
        }
    }