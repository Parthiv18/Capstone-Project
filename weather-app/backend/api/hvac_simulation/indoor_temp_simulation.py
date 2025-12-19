from database.db import (
    get_user_state,
    get_last_updated,
    update_simulated_temp,
)

from datetime import datetime

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


def _parse_weather_row_time(date_str: str) -> datetime | None:
    """Parse weather row `date` like '2025-12-19 14:00:00 EST'.

    We intentionally ignore the timezone suffix because the stored format uses
    abbreviations (EST/EDT) that are not reliably parseable on all platforms.
    """

    if not isinstance(date_str, str):
        return None

    # Drop trailing timezone token (e.g., 'EST').
    base = date_str
    parts = date_str.split(" ")
    if len(parts) >= 3:
        base = " ".join(parts[:2])

    try:
        return datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _select_weather_row(weather_rows: list[dict], target_time: datetime) -> dict | None:
    best_row = None
    best_abs_seconds = None
    for row in weather_rows:
        if not isinstance(row, dict):
            continue
        row_time = _parse_weather_row_time(row.get("date"))
        if row_time is None:
            continue
        abs_seconds = abs((row_time - target_time).total_seconds())
        if best_abs_seconds is None or abs_seconds < best_abs_seconds:
            best_abs_seconds = abs_seconds
            best_row = row
    return best_row


def _estimate_internal_gains_w(
    occupancy: str | None,
    appliances: list[str] | None,
    sim_time: datetime,
    house_size_sqft: float,
) -> float:
    # Very rough internal gains model.
    floor_area_m2 = max(house_size_sqft, 200.0) * 0.092903

    # Baseline plug loads + standby, scales with size.
    base = 2.5 * floor_area_m2  # W

    hour = sim_time.hour
    is_daytime = 8 <= hour <= 20

    occupancy_gain = 0.0
    if occupancy == "home_daytime":
        occupancy_gain = 250.0 if is_daytime else 120.0
    elif occupancy == "away_daytime":
        occupancy_gain = 80.0 if is_daytime else 180.0
    elif occupancy == "hybrid":
        occupancy_gain = 160.0
    else:
        occupancy_gain = 140.0

    appliance_gains = 0.0
    for a in appliances or []:
        if a == "Electric Space Heater":
            appliance_gains += 500.0 if is_daytime else 200.0
        elif a == "Portable Air Conditioner":
            appliance_gains -= 350.0 if is_daytime else 0.0
        elif a in ("Electric Water Heater", "Gas Water Heater"):
            appliance_gains += 150.0
        elif a.startswith("Oven") or a.startswith("Stove"):
            appliance_gains += 120.0 if 16 <= hour <= 19 else 20.0
        elif a.startswith("Clothes Dryer"):
            appliance_gains += 80.0
        elif a.startswith("Washing Machine"):
            appliance_gains += 40.0
        elif a.startswith("Dishwasher"):
            appliance_gains += 40.0 if 18 <= hour <= 22 else 10.0
        elif a.startswith("Electric Vehicle Charger"):
            appliance_gains += 120.0 if 0 <= hour <= 6 else 0.0
        else:
            appliance_gains += 10.0

    return base + occupancy_gain + appliance_gains


def simulate_indoor_temp_rc(
    indoor_temp_c: float,
    outdoor_temp_c: float,
    insulation_quality: str,
    house_size_sqft: float,
    house_age_years: int,
    timestep_minutes: int,
    solar_radiation_w_m2: float = 0.0,
    windspeed_10m_m_s: float = 0.0,
    internal_gains_w: float = 0.0,
) -> float:
    """1st-order RC model in Celsius.

    dT/dt = ( UA*(T_out - T_in) + Q_internal + Q_solar ) / C
    """

    if house_size_sqft <= 0:
        return round(indoor_temp_c, 2)

    floor_area_m2 = house_size_sqft * 0.092903

    ua_per_m2 = {
        "poor": 3.0,
        "average": 2.0,
        "excellent": 1.2,
    }
    if insulation_quality not in ua_per_m2:
        raise ValueError("Invalid insulation_quality")

    age_factor = 1.0 + min(house_age_years / 60.0, 0.6)
    wind_factor = 1.0 + max(0.0, min(windspeed_10m_m_s / 10.0, 2.0)) * 0.12

    UA = ua_per_m2[insulation_quality] * floor_area_m2 * age_factor * wind_factor  # W/K

    # Effective thermal capacitance (thermal mass proxy)
    c_per_m2 = 60_000.0  # J/K/m2
    C = c_per_m2 * floor_area_m2 * (1.0 + min(house_age_years / 80.0, 0.25))  # J/K

    # Solar gains through windows (very rough)
    window_fraction = 0.15
    window_area_m2 = floor_area_m2 * window_fraction
    shgc = {
        "poor": 0.65,
        "average": 0.55,
        "excellent": 0.45,
    }[insulation_quality]
    Q_solar = max(0.0, solar_radiation_w_m2) * window_area_m2 * shgc * 0.35  # W

    dt_s = timestep_minutes * 60.0
    Q = UA * (outdoor_temp_c - indoor_temp_c) + internal_gains_w + Q_solar
    T_next = indoor_temp_c + (dt_s / C) * Q
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

    user_id = state.get("id")
    if user_id is None:
        return {"error": "User id missing"}

    # Use last_updated as the timestamp anchor for selecting a weather row.
    last_updated_raw = state.get("last_updated")
    sim_time = None
    if isinstance(last_updated_raw, str) and last_updated_raw:
        try:
            sim_time = datetime.fromisoformat(last_updated_raw)
        except Exception:
            sim_time = None
    if sim_time is None:
        sim_time = datetime.now()

    weather = _select_weather_row(weather_rows, sim_time) or weather_rows[0]
    if "temperature_2m" not in weather:
        return {"error": "temperature_2m missing from weather data"}

    outdoor_temp = float(weather["temperature_2m"])
    solar_radiation = float(weather.get("solar_radiation") or 0.0)
    windspeed_10m = float(weather.get("windspeed_10m") or 0.0)

    # -------------------------------------------------
    # 4. Indoor Temperature (NO DEFAULT)
    # -------------------------------------------------
    indoor_temp = state.get("simulated_temp")
    if indoor_temp is None:
        # Initialize to outdoor temp so simulation can start immediately.
        # (House variables are required; weather is already present here.)
        indoor_temp = outdoor_temp
        update_simulated_temp(user_id, indoor_temp)

    # -------------------------------------------------
    # 5. Simulate
    # -------------------------------------------------
    timestep_minutes = 5
    internal_gains_w = _estimate_internal_gains_w(
        occupancy=house_data.get("occupancy"),
        appliances=(house.get("appliances") if isinstance(house, dict) else None),
        sim_time=sim_time,
        house_size_sqft=house_size,
    )

    new_temp = simulate_indoor_temp_rc(
        indoor_temp_c=float(indoor_temp),
        outdoor_temp_c=outdoor_temp,
        insulation_quality=insulation,
        house_size_sqft=house_size,
        house_age_years=house_age,
        timestep_minutes=timestep_minutes,
        solar_radiation_w_m2=solar_radiation,
        windspeed_10m_m_s=windspeed_10m,
        internal_gains_w=internal_gains_w,
    )

    # -------------------------------------------------
    # 6. Save
    # -------------------------------------------------
    update_simulated_temp(user_id, new_temp)
    last_updated = get_last_updated(user_id)

    # -------------------------------------------------
    # 7. Response
    # -------------------------------------------------
    return {
        "T_in_prev": indoor_temp,
        "T_in_new": new_temp,
        "T_out": outdoor_temp,
        "hvac_mode": "off",
        "last_updated": last_updated,
        "weather_time": weather.get("date"),
        "insulation_quality": insulation,
        "house_size_sqft": house_size,
        "house_age_years": house_age,
    }
