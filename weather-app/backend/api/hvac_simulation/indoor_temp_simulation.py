"""
Indoor Temperature Simulation Module
Models indoor temperature changes based on outdoor conditions and house properties.
Integrates with HVAC AI for predictive climate control.
"""

from datetime import datetime
from typing import Optional
from database.db import (
    get_user_state, 
    update_simulated_temp, 
    set_hvac_sim, 
    get_hvac_sim,
    get_user_id,
    set_target_setpoint,
    get_target_setpoint
)
from .hvac_physics import (
    generate_hvac_schedule,
    get_current_hvac_action,
    get_upcoming_actions,
    HouseProperties,
    HVACSystem,
    WeatherConditions,
    simulate_temperature_step,
    celsius_to_fahrenheit
)

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


# ============================================================
# HVAC AI Integration
# ============================================================

def run_hvac_ai(username: str, target_temp_c: float = None) -> dict:
    """
    Run the HVAC AI to generate an optimized 24-hour schedule.
    
    Args:
        username: User's username
        target_temp_c: Desired temperature setpoint in Celsius (None = use saved or derive from comfort)
    
    Returns:
        Dict with schedule, current action, and upcoming notifications
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
        return {"error": "House data missing - please submit house information first"}
    
    house_data = house.get("data", house) if isinstance(house, dict) else house
    
    # Get personal comfort preference (1-10, default 5)
    personal_comfort = int(house_data.get("personal_comfort", 5))
    
    # Determine target temperature
    # Priority: 1) Explicitly passed value, 2) Saved setpoint, 3) Derive from comfort
    if target_temp_c is not None:
        # User explicitly set a new target - save it
        set_target_setpoint(user_id, target_temp_c)
    else:
        # Try to get saved setpoint
        saved_setpoint = state.get("target_setpoint")
        if saved_setpoint is not None:
            target_temp_c = saved_setpoint
        else:
            # Derive from personal comfort (comfort 1=18°C, 10=26°C)
            target_temp_c = 18 + (personal_comfort - 1) * (26 - 18) / 9
            target_temp_c = round(target_temp_c, 1)
            # Save the derived value
            set_target_setpoint(user_id, target_temp_c)
    
    # Validate weather data
    raw_weather = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing - please fetch weather first"}
    
    # Handle different weather data formats
    if isinstance(raw_weather, dict):
        weather_rows = raw_weather.get("rows", [])
    elif isinstance(raw_weather, list):
        # Could be list of snapshots or direct rows
        if raw_weather and isinstance(raw_weather[0], dict):
            if "data" in raw_weather[0]:
                # List of snapshots with dates
                latest_snapshot = raw_weather[-1]
                weather_data = latest_snapshot.get("data", {})
                weather_rows = weather_data.get("rows", []) if isinstance(weather_data, dict) else []
            else:
                weather_rows = raw_weather
        else:
            weather_rows = []
    else:
        weather_rows = []
    
    if not weather_rows:
        return {"error": "Weather rows missing"}
    
    # Get current indoor temperature
    indoor_temp = state.get("simulated_temp")
    if indoor_temp is None:
        # Initialize from first weather row
        first_temp = weather_rows[0].get("temperature_2m", 20)
        indoor_temp = float(first_temp)
        update_simulated_temp(user_id, indoor_temp)
    
    # Generate HVAC schedule
    schedule = generate_hvac_schedule(
        house_data=house_data,
        weather_rows=weather_rows,
        current_indoor_temp_c=float(indoor_temp),
        personal_comfort=personal_comfort,
        target_temp_c=target_temp_c
    )
    
    # Get current action and upcoming notifications
    current_action = get_current_hvac_action(schedule)
    upcoming = get_upcoming_actions(schedule, count=5)
    
    # Store schedule in database
    schedule_dict = schedule.to_dict()
    set_hvac_sim(user_id, schedule_dict)
    
    # Format notifications for frontend
    notifications = []
    for action in upcoming:
        notifications.append({
            "mode": action.mode,
            "start_time": action.start_time,
            "end_time": action.end_time,
            "power_kw": action.power_kw,
            "cost": action.cost,
            "reason": action.reason,
            "message": f"HVAC will turn on {action.mode.upper()} at {action.start_time} to {action.end_time} for {action.power_kw:.1f} kWh at ${action.cost:.2f}"
        })
    
    return {
        "schedule": schedule_dict,
        "current_action": {
            "mode": current_action.mode if current_action else "off",
            "power_kw": current_action.power_kw if current_action else 0,
            "reason": current_action.reason if current_action else "No active action"
        } if current_action else {"mode": "off", "power_kw": 0, "reason": "No schedule"},
        "notifications": notifications,
        "summary": {
            "total_cost_24h": round(schedule.total_cost, 2),
            "total_energy_24h_kwh": round(schedule.total_energy_kwh, 2),
            "comfort_score": round(schedule.comfort_score, 1),
            "current_temp_c": round(float(indoor_temp), 1),
            "target_temp_c": target_temp_c
        }
    }


def run_simulation_step_with_hvac(username: str, target_temp_c: float = None) -> dict:
    """
    Run one simulation step with HVAC AI control.
    This is the enhanced version that uses the physics-based model.
    
    Args:
        username: User's username
        target_temp_c: Target temperature (None = use saved setpoint or derive from personal_comfort)
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
    
    house_data = house.get("data", house) if isinstance(house, dict) else house
    
    # Determine target temperature from saved setpoint or personal comfort
    if target_temp_c is None:
        saved_setpoint = state.get("target_setpoint")
        if saved_setpoint is not None:
            target_temp_c = saved_setpoint
        else:
            # Derive from personal comfort (comfort 1=18°C, 10=26°C)
            personal_comfort = int(house_data.get("personal_comfort", 5))
            target_temp_c = 18 + (personal_comfort - 1) * (26 - 18) / 9
            target_temp_c = round(target_temp_c, 1)
            # Save the derived value
            set_target_setpoint(user_id, target_temp_c)
    
    # Validate weather data
    raw_weather = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing"}
    
    # Handle different weather data formats
    if isinstance(raw_weather, dict):
        weather_rows = raw_weather.get("rows", [])
    elif isinstance(raw_weather, list):
        if raw_weather and isinstance(raw_weather[0], dict):
            if "data" in raw_weather[0]:
                latest_snapshot = raw_weather[-1]
                weather_data = latest_snapshot.get("data", {})
                weather_rows = weather_data.get("rows", []) if isinstance(weather_data, dict) else []
            else:
                weather_rows = raw_weather
        else:
            weather_rows = []
    else:
        weather_rows = []
    
    if not weather_rows:
        return {"error": "Weather rows missing"}
    
    # Select current weather row
    sim_time = datetime.now()
    weather_row = _select_weather_row(weather_rows, sim_time) or weather_rows[0]
    
    # Get current indoor temperature
    indoor_temp = state.get("simulated_temp")
    if indoor_temp is None:
        indoor_temp = float(weather_row.get("temperature_2m", 20))
        update_simulated_temp(user_id, indoor_temp)
    
    # Get HVAC schedule (or generate if needed)
    hvac_schedule = state.get("hvac_sim")
    scheduled_mode = "off"
    
    if hvac_schedule:
        current_hour = datetime.now().hour
        actions = hvac_schedule.get("actions", [])
        for action in actions:
            if action.get("hour") == current_hour:
                scheduled_mode = action.get("mode", "off")
                break
    
    # Smart real-time mode override - don't run HVAC if already at/past target
    # This prevents overshooting between schedule updates
    current_mode = scheduled_mode
    deadband = 0.3  # Small tolerance
    
    if scheduled_mode in ["heat", "pre-heat"]:
        # If we're already at or above target, don't heat
        if float(indoor_temp) >= target_temp_c - deadband:
            current_mode = "off"
    elif scheduled_mode in ["cool", "pre-cool"]:
        # If we're already at or below target, don't cool
        if float(indoor_temp) <= target_temp_c + deadband:
            current_mode = "off"
    
    # Build property objects
    house_props = HouseProperties.from_house_data(house_data)
    hvac_system = HVACSystem.from_house_data(house_data)
    weather_cond = WeatherConditions.from_weather_row(weather_row)
    
    # Simulate with 5-minute timestep
    new_temp, power_w, energy_kwh = simulate_temperature_step(
        house=house_props,
        hvac=hvac_system,
        weather=weather_cond,
        indoor_temp_c=float(indoor_temp),
        target_temp_c=target_temp_c,
        hvac_mode=current_mode,
        timestep_seconds=300  # 5 minutes
    )
    
    # Save new temperature
    update_simulated_temp(user_id, new_temp)
    
    # Determine HVAC status for UI (use actual running mode, not scheduled)
    if current_mode in ["heat", "pre-heat"]:
        hvac_status = "heating"
    elif current_mode in ["cool", "pre-cool"]:
        hvac_status = "cooling"
    else:
        hvac_status = "off"
    
    return {
        "T_in_prev": round(float(indoor_temp), 2),
        "T_in_new": round(new_temp, 2),
        "T_out": round(weather_cond.temp_outdoor_c, 2),
        "hvac_mode": hvac_status,
        "hvac_power_kw": round(power_w / 1000, 2),
        "target_temp": target_temp_c
    }
