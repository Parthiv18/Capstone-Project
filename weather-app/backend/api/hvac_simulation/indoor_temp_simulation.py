"""
Indoor Temperature Simulation Module
Models indoor temperature changes based on outdoor conditions and house properties.
"""

from datetime import datetime
from typing import Optional
from database.db import get_user_state, update_simulated_temp

# ============================================================
# Constants
# ============================================================

BASE_INSULATION_RATE = {
    "poor": 0.030,
    "average": 0.015,
    "excellent": 0.007,
}

UA_PER_M2 = {
    "poor": 3.0,
    "average": 2.0,
    "excellent": 1.2,
}

SHGC_VALUES = {
    "poor": 0.65,
    "average": 0.55,
    "excellent": 0.45,
}

SQFT_TO_M2 = 0.092903
DEFAULT_TIMESTEP_MINUTES = 5

# ============================================================
# Temperature Models
# ============================================================

def simulate_indoor_temp_simple(
    indoor_temp: float,
    outdoor_temp: float,
    insulation_quality: str,
    house_size_sqft: float,
    house_age_years: int,
    timestep_minutes: int = DEFAULT_TIMESTEP_MINUTES,
) -> float:
    """
    Simple indoor temperature model.
    Indoor temperature gradually approaches outdoor temperature.
    Rate depends on insulation quality, house size, and house age.
    """
    if insulation_quality not in BASE_INSULATION_RATE:
        raise ValueError(f"Invalid insulation_quality: {insulation_quality}")

    # Base rate from insulation
    k = BASE_INSULATION_RATE[insulation_quality]

    # Age-of-house leakage multiplier (max 50% increase)
    age_multiplier = 1.0 + min(house_age_years / 50.0, 0.5)

    # House size effect (thermal mass proxy, clamped to 0.5-1.5)
    size_multiplier = max(0.5, min(1500.0 / house_size_sqft, 1.5))

    # Effective rate scaled by timestep
    k_eff = k * age_multiplier * size_multiplier * (timestep_minutes / 5)

    T_next = indoor_temp + (outdoor_temp - indoor_temp) * k_eff
    return round(T_next, 2)


def simulate_indoor_temp_basic(
    indoor_temp: float,
    outdoor_temp: float,
    insulation_quality: str,
    house_size_sqft: float,
    timestep_minutes: int = DEFAULT_TIMESTEP_MINUTES,
) -> float:
    """
    Simplified indoor temperature model (no age factor).
    """
    if insulation_quality not in BASE_INSULATION_RATE:
        raise ValueError(f"Invalid insulation_quality: {insulation_quality}")

    if house_size_sqft <= 0:
        return round(indoor_temp, 2)

    k = BASE_INSULATION_RATE[insulation_quality]
    size_multiplier = max(0.5, min(1500.0 / house_size_sqft, 1.5))
    k_eff = k * size_multiplier * (timestep_minutes / 5)

    T_next = indoor_temp + (outdoor_temp - indoor_temp) * k_eff
    return round(T_next, 2)


def simulate_indoor_temp_rc(
    indoor_temp_c: float,
    outdoor_temp_c: float,
    insulation_quality: str,
    house_size_sqft: float,
    house_age_years: int,
    timestep_minutes: int = DEFAULT_TIMESTEP_MINUTES,
    solar_radiation_w_m2: float = 0.0,
    windspeed_10m_m_s: float = 0.0,
    internal_gains_w: float = 0.0,
) -> float:
    """
    1st-order RC thermal model in Celsius.
    dT/dt = (UA*(T_out - T_in) + Q_internal + Q_solar) / C
    """
    if house_size_sqft <= 0:
        return round(indoor_temp_c, 2)

    if insulation_quality not in UA_PER_M2:
        raise ValueError(f"Invalid insulation_quality: {insulation_quality}")

    floor_area_m2 = house_size_sqft * SQFT_TO_M2

    # Calculate UA (heat loss coefficient)
    age_factor = 1.0 + min(house_age_years / 60.0, 0.6)
    wind_factor = 1.0 + max(0.0, min(windspeed_10m_m_s / 10.0, 2.0)) * 0.12
    UA = UA_PER_M2[insulation_quality] * floor_area_m2 * age_factor * wind_factor

    # Thermal capacitance
    c_per_m2 = 60_000.0  # J/K/m²
    C = c_per_m2 * floor_area_m2 * (1.0 + min(house_age_years / 80.0, 0.25))

    # Solar gains through windows
    window_fraction = 0.15
    window_area_m2 = floor_area_m2 * window_fraction
    shgc = SHGC_VALUES[insulation_quality]
    Q_solar = max(0.0, solar_radiation_w_m2) * window_area_m2 * shgc * 0.35

    # Calculate temperature change
    dt_s = timestep_minutes * 60.0
    Q = UA * (outdoor_temp_c - indoor_temp_c) + internal_gains_w + Q_solar
    T_next = indoor_temp_c + (dt_s / C) * Q

    return round(T_next, 2)


# ============================================================
# Weather Row Utilities
# ============================================================

def _parse_weather_row_time(date_str: str) -> Optional[datetime]:
    """
    Parse weather row date like '2025-12-19 14:00:00 EST'.
    Ignores timezone suffix (EST/EDT) for cross-platform compatibility.
    """
    if not isinstance(date_str, str):
        return None

    # Drop trailing timezone token
    parts = date_str.split(" ")
    base = " ".join(parts[:2]) if len(parts) >= 3 else date_str

    try:
        return datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _select_weather_row(weather_rows: list, target_time: datetime) -> Optional[dict]:
    """Select the weather row closest to the target time."""
    best_row = None
    best_delta = None

    for row in weather_rows:
        if not isinstance(row, dict):
            continue
        
        row_time = _parse_weather_row_time(row.get("date"))
        if row_time is None:
            continue
        
        delta = abs((row_time - target_time).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_row = row

    return best_row


# ============================================================
# Simulation Step
# ============================================================

def run_simulation_step(username: str) -> dict:
    """
    Run one simulation step for a user.
    Requires house data and weather data to be present in DB.
    """
    # Get user state
    state = get_user_state(username)
    if state is None:
        return {"error": "User not found"}

    user_id = state.get("id")
    if user_id is None:
        return {"error": "User ID missing"}

    # Validate house data
    house = state.get("house")
    if not house:
        return {"error": "House data missing"}

    # Unwrap nested house data if present
    house_data = house.get("data", house) if isinstance(house, dict) else house

    try:
        insulation = house_data["insulation_quality"]
        house_size = float(house_data["home_size"])
    except (KeyError, TypeError) as e:
        return {"error": f"Missing house field: {e}"}

    # Validate weather data
    raw_weather = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing"}

    weather_rows = raw_weather.get("rows") if isinstance(raw_weather, dict) else raw_weather
    if not weather_rows:
        return {"error": "Weather rows missing"}

    # Determine simulation time
    last_updated = state.get("last_updated")
    sim_time = None
    if isinstance(last_updated, str) and last_updated:
        try:
            sim_time = datetime.fromisoformat(last_updated)
        except Exception:
            pass
    sim_time = sim_time or datetime.now()

    # Select appropriate weather row
    weather = _select_weather_row(weather_rows, sim_time) or weather_rows[0]
    if "temperature_2m" not in weather:
        return {"error": "temperature_2m missing from weather data"}

    outdoor_temp = float(weather["temperature_2m"])

    # Get or initialize indoor temperature
    indoor_temp = state.get("simulated_temp")
    if indoor_temp is None:
        indoor_temp = outdoor_temp
        update_simulated_temp(user_id, indoor_temp)

    # Run simulation
    new_temp = simulate_indoor_temp_basic(
        indoor_temp=float(indoor_temp),
        outdoor_temp=outdoor_temp,
        insulation_quality=insulation,
        house_size_sqft=house_size,
        timestep_minutes=DEFAULT_TIMESTEP_MINUTES,
    )

    # Save new temperature
    update_simulated_temp(user_id, new_temp)

    return {
        "T_in_prev": indoor_temp,
        "T_in_new": new_temp,
    }
