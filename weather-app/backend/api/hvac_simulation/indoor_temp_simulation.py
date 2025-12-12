import json
import datetime
from database.db import get_user_house, get_user_weather, get_user_id, get_simulated_temp, update_simulated_temp


# --- Helper Functions ---

def get_insulation_u_value(quality: str) -> float:
    """
    Map qualitative insulation descriptions to an estimated overall U-value
    (W/m2K) for the building envelope. Lower U => better insulation.

    Values chosen are typical order-of-magnitude estimates for whole-envelope
    heat transfer coefficients:
      - poor: 1.2 W/m2K  (very leaky)
      - average: 0.6 W/m2K
      - excellent: 0.3 W/m2K (well insulated)
    """
    mapping = {
        "poor": 1.2,
        "average": 0.6,
        "excellent": 0.3
    }
    return mapping.get(str(quality).lower(), 0.6)

def simulate_indoor_temp(
    indoor_temp: float,
    outdoor_temp: float,
    windspeed: float,
    solar_radiation: float,
    floor_area_m2: float,
    house_volume: float,
    u_value: float,
    occupants: int = 2,
    hvac_power_w: float = 0.0,
    thermostat_setpoint: float | None = None,
    timestep_s: int = 3600,
) -> dict:
    """
    Lumped RC thermal model (discrete time) for one timestep.

    Governing equation (power balance):
      C * dT/dt = (T_out - T_in)/R + Q_internal + Q_solar + Q_hvac

    Discrete update:
      T_next = T_in + (dt / C) * ( (T_out - T_in)/R + Q_int + Q_solar + Q_hvac )

    Units:
      - R: K/W (derived from U-value and envelope area)
      - C: J/K (thermal capacitance)
      - Q_*: W
      - dt: seconds

    Returns a dict with `T_next` and component breakdown for diagnostics.
    """
    # --- Geometry estimates ---
    # Envelope area: approximate walls+roof+windows as ~3x floor area
    envelope_area_m2 = max(floor_area_m2 * 3.0, 20.0)

    # Apply wind effect: increase effective U with wind stripping
    wind_factor = 1.0 + min(windspeed / 10.0, 1.0)
    effective_u = u_value * wind_factor

    # Thermal resistance of envelope (K/W)
    R_envelope = 1.0 / (effective_u * envelope_area_m2)

    # --- Thermal capacitance ---
    # Air heat capacity (approx): rho_air * volume * cp_air
    rho_air = 1.225  # kg/m3
    cp_air = 1005.0  # J/(kg*K)
    C_air = house_volume * rho_air * cp_air

    # Building fabric thermal mass: empirical per-floor-area heat capacity (J/K)
    # Typical values ~ 1e5 - 3e5 J/m2K; use 165000 J/m2K as compromise
    C_building = floor_area_m2 * 165000.0
    C_total = C_air + C_building

    # --- Internal gains (W) ---
    # Occupant sensible heat ~ 70-100 W each + appliances/lighting ~ 300 W
    Q_internal = occupants * 80.0 + 300.0

    # --- Solar gains (W) ---
    # Use an aperture area (windows/roof) fraction of floor area
    aperture_area = max(floor_area_m2 * 0.2, 1.0)
    solar_transmittance = 0.7
    Q_solar = solar_radiation * aperture_area * solar_transmittance

    # --- HVAC contribution (W) ---
    Q_hvac = 0.0
    if hvac_power_w and thermostat_setpoint is not None:
        # Basic thermostat: apply full available power in direction to reach setpoint
        if indoor_temp < thermostat_setpoint:
            Q_hvac = abs(hvac_power_w)
        elif indoor_temp > thermostat_setpoint:
            Q_hvac = -abs(hvac_power_w)

    # Passive heat flow (W) from outdoor to indoor based on temperature diff
    Q_envelope = (outdoor_temp - indoor_temp) / R_envelope

    # Net power into thermal mass (W)
    Q_net = Q_envelope + Q_internal + Q_solar + Q_hvac

    # Temperature change
    delta_T = (timestep_s / C_total) * Q_net
    T_next = indoor_temp + delta_T

    return {
        "T_next": round(T_next, 2),
        "components": {
            "Q_envelope_W": round(Q_envelope, 1),
            "Q_internal_W": round(Q_internal, 1),
            "Q_solar_W": round(Q_solar, 1),
            "Q_hvac_W": round(Q_hvac, 1),
            "C_total_JperK": round(C_total, 1),
            "R_envelope_KperW": round(R_envelope, 6)
        }
    }

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
    occupants = int(house_data.get("occupants", 2))
    hvac_power_w = float(house_data.get("hvac_power_w", 0.0))
    thermostat_setpoint = None
    try:
        thermostat_setpoint = float(house_data.get("thermostat_setpoint", "nan"))
    except Exception:
        thermostat_setpoint = None

    # Prepare parameters for the formula
    # Convert sqft to m2 (1 sqft = 0.092903 m2) and use ceiling height if provided
    floor_area_m2 = area_sqft * 0.092903
    ceiling_height = float(house_data.get("ceiling_height", 2.5))
    house_volume = floor_area_m2 * ceiling_height
    u_value = get_insulation_u_value(insulation_quality)

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

    # 3. Get Current Indoor State
    # If no history exists (first run), start at a standard comfortable temp (e.g. 21C)
    T_in = get_simulated_temp(user_id)
    if T_in is None:
        T_in = 21.0

    # 4. Calculate Next Temperature using the RC model
    sim_result = simulate_indoor_temp(
        indoor_temp=T_in,
        outdoor_temp=T_out,
        windspeed=wind_speed,
        solar_radiation=solar_rad,
        floor_area_m2=floor_area_m2,
        house_volume=house_volume,
        u_value=u_value,
        occupants=occupants,
        hvac_power_w=hvac_power_w,
        thermostat_setpoint=thermostat_setpoint,
        timestep_s=3600,
    )
    T_next = sim_result.get("T_next", round(T_in, 2))

    # 5. Update Database with the new simulated temperature
    update_simulated_temp(user_id, T_next)

    # 6. Return result structure required by frontend + diagnostics
    return {
        "timestamp": weather_row.get("date", "Unknown"),
        "T_in_prev": T_in,
        "T_in_new": T_next,
        "simulation_components": sim_result.get("components", {})
    }