from database.db import (
    get_user_state,
    update_simulated_temp,
)

# =========================================================
# SIMPLE INDOOR TEMPERATURE MODEL
# =========================================================

def simulate_indoor_temp_simple(
    indoor_temp: float,
    outdoor_temp: float,
    insulation_quality: str,
    house_size_sqft: float,
    house_age_years: int,
    timestep_minutes: int,
) -> float:
    """
    Indoor temperature gradually approaches outdoor temperature.
    Rate depends on insulation quality, house size, and house age.
    """

    BASE_INSULATION_RATE = {
        "poor": 0.030,
        "average": 0.015,
        "excellent": 0.007,
    }

    if insulation_quality not in BASE_INSULATION_RATE:
        raise ValueError("Invalid insulation_quality")

    # Base rate from insulation
    k = BASE_INSULATION_RATE[insulation_quality]

    # Age-of-house leakage multiplier
    age_multiplier = 1.0 + min(house_age_years / 50.0, 0.5)

    # House size effect (thermal mass proxy)
    size_multiplier = 1500.0 / house_size_sqft
    size_multiplier = max(0.5, min(size_multiplier, 1.5))

    # Effective rate
    k_eff = k * age_multiplier * size_multiplier

    # Scale by timestep
    timestep_scale = timestep_minutes / 5

    T_next = indoor_temp + (outdoor_temp - indoor_temp) * k_eff * timestep_scale
    return round(T_next, 2)


# =========================================================
# SIMULATION STEP
# =========================================================

def run_simulation_step(username: str):
    """
    Runs one simulation step.
    Requires ALL values to be present in DB.
    """

    # -------------------------------------------------
    # 1. User
    # -------------------------------------------------
    state = get_user_state(username)
    if state is None:
        return {"error": "User not found"}

    # -------------------------------------------------
    # 2. House Data (NO DEFAULTS)
    # -------------------------------------------------
    house = state.get("house")
    if not house:
        return {"error": "House data missing"}
    # Frontend stores the house payload as {"data": {...}, "appliances": [...]}.
    # Unwrap `data` if present so keys like `insulation_quality` are accessible.
    if isinstance(house, dict) and "data" in house and isinstance(house["data"], dict):
        house_data = house["data"]
    else:
        house_data = house

    try:
        insulation = house_data["insulation_quality"]
        house_size = float(house_data["home_size"])        # sqft
        house_age = int(house_data["age_of_house"])         # years
    except KeyError as e:
        return {"error": f"Missing house field: {e.args[0]}"}

    # -------------------------------------------------
    # 3. Weather Data (NO DEFAULTS)
    # -------------------------------------------------
    raw_weather = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing"}

    if isinstance(raw_weather, dict):
        weather_rows = raw_weather.get("rows")
    else:
        weather_rows = raw_weather

    if not weather_rows or len(weather_rows) == 0:
        return {"error": "Weather rows missing"}

    weather = weather_rows[0]

    if "temperature_2m" not in weather:
        return {"error": "temperature_2m missing from weather data"}

    outdoor_temp = float(weather["temperature_2m"])

    # -------------------------------------------------
    # 4. Indoor Temperature (NO DEFAULT)
    # -------------------------------------------------
    indoor_temp = state.get("simulated_temp")
    if indoor_temp is None:
        return {"error": "Indoor temperature not initialized"}

    # -------------------------------------------------
    # 5. Simulate
    # -------------------------------------------------
    new_temp = simulate_indoor_temp_simple(
        indoor_temp=indoor_temp,
        outdoor_temp=outdoor_temp,
        insulation_quality=insulation,
        house_size_sqft=house_size,
        house_age_years=house_age,
        timestep_minutes=5,
    )

    # -------------------------------------------------
    # 6. Save
    # -------------------------------------------------
    user_id = state.get("id")
    update_simulated_temp(user_id, new_temp)

    # -------------------------------------------------
    # 7. Response
    # -------------------------------------------------
    return {
        "T_in_prev": indoor_temp,
        "T_in_new": new_temp,
        "T_out": outdoor_temp,
        "insulation_quality": insulation,
        "house_size_sqft": house_size,
        "house_age_years": house_age,
    }
