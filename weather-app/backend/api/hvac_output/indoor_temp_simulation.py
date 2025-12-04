import json
import datetime
from database.db import get_user_house, get_user_weather, get_user_id, get_simulated_temp, update_simulated_temp


# --- Helper Functions ---

def get_insulation_coefficient(quality: str) -> float:
    """
    Maps a qualitative description of insulation to a heat transfer coefficient (k).
    The coefficient represents the rate of heat transfer. A lower 'k' means
    better insulation. The unit is roughly 'fraction of temperature difference
    lost per hour' for a reference-sized house.

    - "excellent": Corresponds to modern, high-grade insulation. Very slow heat loss.
    - "average": Corresponds to standard insulation found in many homes.
    - "poor": Corresponds to little or no insulation. Rapid heat loss.
    """
    mapping = {
        "poor": 0.05,
        "average": 0.03,
        "excellent": 0.01
    }
    return mapping.get(str(quality).lower(), 0.03)

def simulate_indoor_temp(
    indoor_temp: float,
    outdoor_temp: float,
    windspeed: float,
    solar_radiation: float,
    house_volume: float,
    insulation_k: float
) -> float:
    """
    Calculates the next hour's indoor temperature based on a simplified thermal model.

    This model considers several factors:
    - Heat transfer due to the indoor/outdoor temperature difference.
    - Increased heat loss from wind (convection).
    - The house's thermal mass (larger homes change temperature slower).
    - Heat gain from solar radiation.
    - A small, constant internal heat gain from occupants and appliances.

    Returns:
        float: The predicted indoor temperature for the next hour.
    """
    # 1. Heat loss/gain from temperature difference (conduction/convection)
    # This is the primary driver of temperature change, based on Newton's Law of Cooling.
    # 'k' is the overall heat transfer coefficient, starting with the insulation value.
    k = insulation_k

    # 2. Wind's effect on heat loss
    # Higher wind speed strips heat away from the building's surface faster.
    # The divisor '50' is a tuning factor; a smaller number means wind has more impact.
    k *= (1 + windspeed / 50)

    # 3. Thermal Mass (Inertia)
    # Larger houses (more volume) have more mass and change temperature more slowly.
    # We model this by reducing the effective heat transfer rate.
    # A standard 1500 sqft house has a volume of ~350 m^3. We'll use this as a reference.
    thermal_mass_factor = max(house_volume / 350, 1)
    k /= thermal_mass_factor

    # Calculate the temperature change due to conduction and convection
    delta_conduction_convection = (outdoor_temp - indoor_temp) * k

    # 4. Solar Gain (Radiation)
    # Sunlight shining on the house warms it up. This is proportional to the solar
    # radiation intensity (in W/m^2). We'll assume a certain effective surface
    # area for heat gain that is related to the house size.
    # The '/ 1000' and other factors are for scaling and unit conversion.
    effective_solar_area = house_volume / 10  # A rough proxy for wall/roof area
    solar_gain_effect = (solar_radiation / 1000) * (effective_solar_area / 100) * 0.5
    
    # 5. Internal Heat Gain
    # People, lights, and appliances generate a small amount of constant heat.
    # We'll add a small, constant temperature rise, e.g., 0.05 degrees C per hour.
    internal_gain = 0.05

    # 6. Calculate the final temperature for the next hour
    # Combine the starting temperature with all the calculated changes.
    next_temp = indoor_temp + delta_conduction_convection + solar_gain_effect + internal_gain

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
    # Convert sqft to m2 (1 sqft = 0.092903 m2) and assume 2.5m ceiling height
    house_volume = (area_sqft * 0.092903) * 2.5
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

    # 3. Get Current Indoor State
    # If no history exists (first run), start at a standard comfortable temp (e.g. 21C)
    T_in = get_simulated_temp(user_id)
    if T_in is None:
        T_in = 21.0

    # 4. Calculate Next Temperature using the new simplified logic
    T_next = simulate_indoor_temp(
        indoor_temp=T_in,
        outdoor_temp=T_out,
        windspeed=wind_speed,
        solar_radiation=solar_rad,
        house_volume=house_volume,
        insulation_k=insulation_k
    )

    # 5. Update Database with the new simulated temperature
    update_simulated_temp(user_id, T_next)

    # 6. Return result structure required by frontend
    return {
        "timestamp": weather_row.get("date", "Unknown"),
        "T_in_prev": T_in,
        "T_in_new": T_next,
    }