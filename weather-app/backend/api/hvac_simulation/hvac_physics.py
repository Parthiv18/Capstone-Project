"""
HVAC Physics Engine & Predictive AI Controller
Implements comprehensive thermal modeling and intelligent HVAC scheduling.

Based on RC (Resistor-Capacitor) thermal model with:
- Home thermal characteristics (thermal mass as capacitor)
- Environmental heat transfer (conductive, wind, solar)
- Humidity and power consumption modeling
- Predictive cost optimization algorithm
"""

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

# ============================================================
# Physical Constants
# ============================================================

AIR_DENSITY = 1.2  # kg/m³ (at sea level, ~20°C)
AIR_SPECIFIC_HEAT = 1005  # J/(kg·K)
SQFT_TO_M2 = 0.092903
DEFAULT_CEILING_HEIGHT_M = 2.7  # ~9 ft
ELECTRICITY_COST_KWH = 0.15  # $/kWh

# ============================================================
# House Property Mappings
# ============================================================

# U-value (W/m²K) - Heat transfer coefficient
U_VALUES = {
    "poor": 1.2,      # Old, poorly insulated
    "average": 0.5,   # Standard code-compliant
    "excellent": 0.15 # Passive house level
}

# R-value (m²K/W) - Thermal resistance (inverse of U)
R_VALUES = {
    "poor": 2.0,
    "average": 5.0,
    "excellent": 10.0
}

# Solar Heat Gain Coefficient
SHGC_VALUES = {
    "poor": 0.80,     # Single pane
    "average": 0.50,  # Double pane
    "excellent": 0.25 # Low-E coated
}

# Air Changes per Hour (base infiltration)
ACH_BASE = {
    "poor": 1.0,      # Leaky/old house
    "average": 0.4,   # Average house
    "excellent": 0.1  # Airtight/new
}

# Wind factor for infiltration
KW_VALUES = {
    "poor": 0.04,
    "average": 0.02,
    "excellent": 0.005
}

# Thermal mass coefficient (J/K per m² of floor area)
THERMAL_MASS_PER_M2 = {
    "poor": 50000,    # Light construction
    "average": 80000, # Standard construction
    "excellent": 120000  # Heavy/masonry construction
}

# Latent heat coefficient (W per % humidity difference)
KH_VALUES = {
    "small": 65,      # < 1000 sqft
    "medium": 150,    # 1000-2500 sqft
    "large": 300      # > 2500 sqft
}

# HVAC COP (Coefficient of Performance) by type and age
HVAC_COP_BASE = {
    "central_ac": 3.5,
    "heat_pump": 4.0,
    "window_unit": 2.5,
    "mini_split": 4.5,
    "furnace": 0.95,  # Efficiency for heating (not COP)
}

# Comfort weight (λ) based on personal comfort preference (1-10)
LAMBDA_VALUES = {
    1: 0.05,   # Very eco-focused
    2: 0.1,
    3: 0.2,
    4: 0.3,
    5: 0.5,    # Balanced
    6: 0.6,
    7: 0.7,
    8: 0.8,
    9: 0.9,
    10: 1.0   # Maximum comfort
}

# ============================================================
# Data Classes
# ============================================================

@dataclass
class HouseProperties:
    """Physical properties of the house."""
    floor_area_m2: float
    volume_m3: float
    surface_area_m2: float
    window_area_m2: float
    u_value: float
    r_value: float
    shgc: float
    ach_base: float
    kw: float
    thermal_mass: float  # J/K (C_home)
    kh: float  # Latent heat coefficient
    
    @classmethod
    def from_house_data(cls, house_data: dict) -> "HouseProperties":
        """Create HouseProperties from user's house data."""
        home_size_sqft = float(house_data.get("home_size", 1500))
        insulation = house_data.get("insulation_quality", "average").lower()
        age = int(house_data.get("age_of_house", 20))
        
        floor_area_m2 = home_size_sqft * SQFT_TO_M2
        volume_m3 = floor_area_m2 * DEFAULT_CEILING_HEIGHT_M
        
        # Estimate surface area (simplified box model)
        side_length = math.sqrt(floor_area_m2)
        wall_area = 4 * side_length * DEFAULT_CEILING_HEIGHT_M
        surface_area_m2 = floor_area_m2 + wall_area  # roof + walls
        
        # Window area (typical 15% of floor area)
        window_area_m2 = floor_area_m2 * 0.15
        
        # Get base values from insulation quality
        u_value = U_VALUES.get(insulation, U_VALUES["average"])
        r_value = R_VALUES.get(insulation, R_VALUES["average"])
        shgc = SHGC_VALUES.get(insulation, SHGC_VALUES["average"])
        ach_base = ACH_BASE.get(insulation, ACH_BASE["average"])
        kw = KW_VALUES.get(insulation, KW_VALUES["average"])
        
        # Age degradation factor (up to 50% worse for 50+ year old houses)
        age_factor = 1.0 + min(age / 50.0, 0.5)
        u_value *= age_factor
        ach_base *= age_factor
        
        # Thermal mass
        thermal_mass = THERMAL_MASS_PER_M2.get(insulation, THERMAL_MASS_PER_M2["average"]) * floor_area_m2
        
        # Latent heat coefficient based on size
        if home_size_sqft < 1000:
            kh = KH_VALUES["small"]
        elif home_size_sqft < 2500:
            kh = KH_VALUES["medium"]
        else:
            kh = KH_VALUES["large"]
        
        return cls(
            floor_area_m2=floor_area_m2,
            volume_m3=volume_m3,
            surface_area_m2=surface_area_m2,
            window_area_m2=window_area_m2,
            u_value=u_value,
            r_value=r_value,
            shgc=shgc,
            ach_base=ach_base,
            kw=kw,
            thermal_mass=thermal_mass,
            kh=kh
        )


@dataclass
class WeatherConditions:
    """Weather conditions at a specific time."""
    timestamp: datetime
    temp_outdoor_c: float
    humidity: float = 50.0
    solar_radiation: float = 0.0  # W/m²
    windspeed: float = 0.0  # m/s
    precipitation: float = 0.0  # mm
    rain: float = 0.0  # mm
    snowfall: float = 0.0  # cm
    dew_point_c: float = 10.0
    
    @property
    def is_raining(self) -> bool:
        return self.rain > 0.1
    
    @property
    def is_snowing(self) -> bool:
        return self.snowfall > 0.1
    
    @property
    def wet_bulb_temp_c(self) -> float:
        """Calculate wet bulb temperature (approximation)."""
        # Stull formula approximation
        T = self.temp_outdoor_c
        RH = self.humidity
        Tw = T * math.atan(0.151977 * math.sqrt(RH + 8.313659)) + \
             math.atan(T + RH) - math.atan(RH - 1.676331) + \
             0.00391838 * (RH ** 1.5) * math.atan(0.023101 * RH) - 4.686035
        return Tw
    
    @classmethod
    def from_weather_row(cls, row: dict) -> "WeatherConditions":
        """Create WeatherConditions from a weather API row."""
        # Parse timestamp
        date_str = row.get("date", "")
        timestamp = datetime.now()
        if date_str:
            try:
                parts = date_str.split(" ")
                base = " ".join(parts[:2]) if len(parts) >= 2 else date_str
                timestamp = datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
            except:
                pass
        
        return cls(
            timestamp=timestamp,
            temp_outdoor_c=float(row.get("temperature_2m", 20)),
            humidity=float(row.get("humidity_2m", 50)),
            solar_radiation=float(row.get("solar_radiation", 0)),
            windspeed=float(row.get("windspeed_10m", 0)),
            precipitation=float(row.get("precipitation", 0)),
            rain=float(row.get("rain", 0)),
            snowfall=float(row.get("snowfall", 0)),
            dew_point_c=float(row.get("dew_point_2m", 10))
        )


@dataclass
class HVACSystem:
    """HVAC system properties."""
    hvac_type: str
    hvac_age: int
    capacity_heating_w: float = 10000  # Default 10kW
    capacity_cooling_w: float = 10000
    
    @classmethod
    def from_house_data(cls, house_data: dict) -> "HVACSystem":
        """Create HVACSystem from user's house data."""
        hvac_type = house_data.get("hvac_type", "central_ac").lower().replace(" ", "_")
        hvac_age = int(house_data.get("hvac_age", 10) or 10)
        home_size = float(house_data.get("home_size", 1500))
        
        # Estimate capacity based on home size (rule of thumb: 20 BTU/sqft)
        # 1 BTU/hr ≈ 0.293 W
        capacity_w = home_size * 20 * 0.293
        
        return cls(
            hvac_type=hvac_type,
            hvac_age=hvac_age,
            capacity_heating_w=capacity_w,
            capacity_cooling_w=capacity_w
        )
    
    def get_cop_cooling(self, outdoor_temp_c: float) -> float:
        """Get COP for cooling, adjusted for outdoor temperature."""
        base_cop = HVAC_COP_BASE.get(self.hvac_type, 3.0)
        
        # Age degradation (2% per year, max 40%)
        age_factor = max(0.6, 1.0 - 0.02 * self.hvac_age)
        
        # Temperature derating (COP drops in hot weather)
        # Base COP at 35°C, decreases 2% per degree above
        if outdoor_temp_c > 35:
            temp_factor = max(0.5, 1.0 - 0.02 * (outdoor_temp_c - 35))
        else:
            temp_factor = 1.0
        
        return base_cop * age_factor * temp_factor
    
    def get_cop_heating(self, outdoor_temp_c: float) -> float:
        """Get COP for heating, adjusted for outdoor temperature."""
        if self.hvac_type == "furnace":
            # Furnace efficiency doesn't depend much on outdoor temp
            return HVAC_COP_BASE["furnace"] * max(0.6, 1.0 - 0.01 * self.hvac_age)
        
        base_cop = HVAC_COP_BASE.get(self.hvac_type, 3.0)
        
        # Age degradation
        age_factor = max(0.6, 1.0 - 0.02 * self.hvac_age)
        
        # Heat pumps lose efficiency in cold weather
        if outdoor_temp_c < 0:
            temp_factor = max(0.3, 1.0 - 0.03 * abs(outdoor_temp_c))
        else:
            temp_factor = 1.0
        
        return base_cop * age_factor * temp_factor


@dataclass
class HVACAction:
    """Represents a scheduled HVAC action."""
    hour: int
    mode: str  # "heat", "cool", "pre-heat", "pre-cool", "off"
    start_time: str
    end_time: str
    power_kw: float
    cost: float
    reason: str
    predicted_temp_c: float
    target_temp_c: float


@dataclass
class HVACSchedule:
    """24-hour HVAC schedule with notifications."""
    actions: List[HVACAction] = field(default_factory=list)
    total_cost: float = 0.0
    total_energy_kwh: float = 0.0
    comfort_score: float = 0.0
    generated_at: str = ""
    
    def to_dict(self) -> dict:
        return {
            "actions": [asdict(a) for a in self.actions],
            "total_cost": round(self.total_cost, 2),
            "total_energy_kwh": round(self.total_energy_kwh, 2),
            "comfort_score": round(self.comfort_score, 2),
            "generated_at": self.generated_at
        }


# ============================================================
# Heat Transfer Calculations
# ============================================================

def calc_q_conductive(
    house: HouseProperties,
    weather: WeatherConditions,
    indoor_temp_c: float
) -> float:
    """
    Calculate conductive heat transfer through building envelope.
    Q_conductive = U_eff × A_home × (T_boundary - T_in)
    
    Adjusts for rain (wet bulb temp) and snow (added R-value).
    """
    u_eff = house.u_value
    t_boundary = weather.temp_outdoor_c
    
    # Rain: use wet bulb temperature (evaporative cooling effect)
    if weather.is_raining:
        t_boundary = weather.wet_bulb_temp_c
    
    # Snow: add thermal resistance from snow layer
    if weather.is_snowing:
        # Snow R-value: approximately 1 m²K/W per 10cm of snow
        r_snow = weather.snowfall * 0.1  # snowfall in cm
        r_total = house.r_value + r_snow
        u_eff = 1.0 / r_total if r_total > 0 else house.u_value
    
    q_conductive = u_eff * house.surface_area_m2 * (t_boundary - indoor_temp_c)
    return q_conductive


def calc_q_wind(
    house: HouseProperties,
    weather: WeatherConditions,
    indoor_temp_c: float
) -> float:
    """
    Calculate heat transfer due to wind infiltration.
    ACH = ACH_base + k_w × Wind
    Q_wind = ρ_air × C_p × V_home × ACH / 3600 × (T_out - T_in)
    """
    ach = house.ach_base + house.kw * weather.windspeed
    
    q_wind = (AIR_DENSITY * AIR_SPECIFIC_HEAT * house.volume_m3 * ach / 3600 
              * (weather.temp_outdoor_c - indoor_temp_c))
    return q_wind


def calc_q_solar(
    house: HouseProperties,
    weather: WeatherConditions
) -> float:
    """
    Calculate solar heat gain through windows.
    Q_solar = A_windows × SHGC × Solar
    
    Reduced if snow on roof (high albedo).
    """
    solar = weather.solar_radiation
    
    # If significant snow, reduce solar gain (reflective surfaces)
    if weather.snowfall > 5:  # > 5cm snow
        solar *= 0.5
    
    q_solar = house.window_area_m2 * house.shgc * solar
    return max(0, q_solar)


def calc_q_hvac(
    hvac: HVACSystem,
    mode: str,
    indoor_temp_c: float,
    target_temp_c: float,
    outdoor_temp_c: float,
    deadband: float = 0.3
) -> Tuple[float, float]:
    """
    Calculate HVAC heat output and power consumption.
    Returns (Q_hvac in Watts, Power draw in Watts)
    
    Q_hvac: positive = adding heat, negative = removing heat
    
    Uses smart proportional control with deadband to prevent overshooting.
    HVAC reduces output as temperature approaches target.
    """
    if mode == "off":
        return 0.0, 0.0
    
    temp_diff = target_temp_c - indoor_temp_c
    
    if mode in ["heat", "pre-heat"]:
        # Stop heating if at or above target (with small buffer to prevent overshoot)
        if temp_diff <= -deadband:
            return 0.0, 0.0
        
        # Reduce output significantly as we approach target to prevent overshoot
        if temp_diff <= 0:
            # We're at target or slightly above - minimal output
            output_fraction = 0.05
        elif temp_diff <= deadband:
            # Very close to target - use minimal output
            output_fraction = 0.1
        elif temp_diff <= 1.0:
            # Within 1°C - low output
            output_fraction = 0.2 + (temp_diff - deadband) * 0.2
        elif temp_diff <= 2.0:
            # Within 2°C - moderate output
            output_fraction = 0.4 + (temp_diff - 1.0) * 0.3
        else:
            # More than 2°C away - higher output, capped at 90%
            output_fraction = min(0.9, 0.7 + (temp_diff - 2.0) * 0.1)
        
        q_hvac = hvac.capacity_heating_w * output_fraction
        cop = hvac.get_cop_heating(outdoor_temp_c)
        power_draw = q_hvac / cop if cop > 0 else q_hvac
        return q_hvac, power_draw
    
    elif mode in ["cool", "pre-cool"]:
        # Stop cooling if at or below target (with small buffer to prevent overshoot)
        if temp_diff >= deadband:
            return 0.0, 0.0
        
        # Reduce output significantly as we approach target to prevent overshoot
        abs_diff = abs(temp_diff)
        if temp_diff >= 0:
            # We're at target or slightly below - minimal output
            output_fraction = 0.05
        elif abs_diff <= deadband:
            # Very close to target - use minimal output
            output_fraction = 0.1
        elif abs_diff <= 1.0:
            # Within 1°C - low output
            output_fraction = 0.2 + (abs_diff - deadband) * 0.2
        elif abs_diff <= 2.0:
            # Within 2°C - moderate output
            output_fraction = 0.4 + (abs_diff - 1.0) * 0.3
        else:
            # More than 2°C away - higher output, capped at 90%
            output_fraction = min(0.9, 0.7 + (abs_diff - 2.0) * 0.1)
        
        q_hvac = -hvac.capacity_cooling_w * output_fraction  # Negative = removing heat
        cop = hvac.get_cop_cooling(outdoor_temp_c)
        power_draw = abs(q_hvac) / cop if cop > 0 else abs(q_hvac)
        return q_hvac, power_draw
    
    return 0.0, 0.0


def calc_q_latent(
    house: HouseProperties,
    indoor_humidity: float,
    target_humidity: float = 50.0
) -> float:
    """
    Calculate latent heat load for dehumidification.
    Q_latent = k_h × (Humidity_in - Humidity_target)
    
    This represents energy needed to remove moisture (doesn't affect dry-bulb temp).
    """
    if indoor_humidity <= target_humidity:
        return 0.0
    
    return house.kh * (indoor_humidity - target_humidity)


# ============================================================
# Temperature Simulation
# ============================================================

def simulate_temperature_step(
    house: HouseProperties,
    hvac: HVACSystem,
    weather: WeatherConditions,
    indoor_temp_c: float,
    target_temp_c: float,
    hvac_mode: str,
    timestep_seconds: float = 3600  # 1 hour default
) -> Tuple[float, float, float]:
    """
    Simulate one timestep of indoor temperature change.
    
    C_home × dT/dt = Q_solar + Q_wind + Q_conductive + Q_HVAC
    
    Returns: (new_temp_c, hvac_power_w, hvac_energy_kwh)
    
    Includes smart overshoot protection - won't exceed target by more than 0.5°C.
    """
    # Calculate all heat flows
    q_solar = calc_q_solar(house, weather)
    q_wind = calc_q_wind(house, weather, indoor_temp_c)
    q_conductive = calc_q_conductive(house, weather, indoor_temp_c)
    q_hvac, power_draw = calc_q_hvac(hvac, hvac_mode, indoor_temp_c, target_temp_c, weather.temp_outdoor_c)
    
    # Total heat flow
    q_total = q_solar + q_wind + q_conductive + q_hvac
    
    # Temperature change: dT = (Q_total × dt) / C_home
    dt = (q_total * timestep_seconds) / house.thermal_mass if house.thermal_mass > 0 else 0
    new_temp = indoor_temp_c + dt
    
    # Smart overshoot protection - clamp temperature to prevent overshooting target
    max_overshoot = 0.5  # Allow only 0.5°C overshoot for comfort
    if hvac_mode in ["heat", "pre-heat"]:
        # When heating, don't let temp go more than max_overshoot above target
        if new_temp > target_temp_c + max_overshoot:
            new_temp = target_temp_c + max_overshoot
            # Reduce power proportionally (we would have stopped sooner)
            power_draw *= 0.3
    elif hvac_mode in ["cool", "pre-cool"]:
        # When cooling, don't let temp go more than max_overshoot below target
        if new_temp < target_temp_c - max_overshoot:
            new_temp = target_temp_c - max_overshoot
            # Reduce power proportionally
            power_draw *= 0.3
    
    # Energy consumption for this timestep
    energy_kwh = (power_draw * timestep_seconds) / (1000 * 3600)  # W·s to kWh
    
    return round(new_temp, 2), round(power_draw, 2), round(energy_kwh, 4)


def predict_temperature(
    house: HouseProperties,
    hvac: HVACSystem,
    weather_forecast: List[WeatherConditions],
    start_temp_c: float,
    target_temp_c: float,
    hvac_mode: str = "off"
) -> List[float]:
    """
    Predict indoor temperatures for each hour in the forecast.
    """
    temps = [start_temp_c]
    current_temp = start_temp_c
    
    for weather in weather_forecast:
        new_temp, _, _ = simulate_temperature_step(
            house, hvac, weather, current_temp, target_temp_c, hvac_mode
        )
        temps.append(new_temp)
        current_temp = new_temp
    
    return temps


# ============================================================
# Predictive HVAC Controller
# ============================================================

def calculate_cost_function(
    power_kwh: float,
    electricity_price: float,
    indoor_temp_c: float,
    target_temp_c: float,
    lambda_comfort: float
) -> float:
    """
    Calculate the cost function J for optimization.
    J = P_HVAC × Price + λ × |T_in - T_set|
    
    Goal: Minimize J
    """
    energy_cost = power_kwh * electricity_price
    discomfort_cost = lambda_comfort * abs(indoor_temp_c - target_temp_c)
    return energy_cost + discomfort_cost


def determine_hvac_mode(
    house: HouseProperties,
    hvac: HVACSystem,
    weather_current: WeatherConditions,
    weather_next: Optional[WeatherConditions],
    weather_future: Optional[WeatherConditions],
    indoor_temp_c: float,
    target_temp_c: float,
    delta: float,  # Deadband tolerance
    current_price: float,
    future_price: float,
    lambda_comfort: float
) -> Tuple[str, str]:
    """
    Determine optimal HVAC mode using predictive heuristic algorithm.
    
    Returns: (mode, reason)
    """
    # Predict temperature for next hour (HVAC off)
    if weather_next:
        t_pred_1, _, _ = simulate_temperature_step(
            house, hvac, weather_next, indoor_temp_c, target_temp_c, "off"
        )
    else:
        t_pred_1 = indoor_temp_c
    
    # Predict temperature for 2 hours ahead (HVAC off)
    if weather_future:
        t_pred_2, _, _ = simulate_temperature_step(
            house, hvac, weather_future, t_pred_1, target_temp_c, "off"
        )
    else:
        t_pred_2 = t_pred_1
    
    upper_limit = target_temp_c + delta
    lower_limit = target_temp_c - delta
    
    # "Off" Condition: Temperature within comfort band
    if lower_limit <= t_pred_1 <= upper_limit:
        # Check for pre-conditioning opportunities
        
        # Pre-Cool: Future temp will exceed upper limit AND current price is cheaper
        if weather_future and t_pred_2 > upper_limit and current_price < future_price:
            return "pre-cool", f"Pre-cooling: future temp {t_pred_2:.1f}°C exceeds comfort, cheaper now (${current_price:.2f} vs ${future_price:.2f})"
        
        # Pre-Heat: Future temp will drop below lower limit AND current price is cheaper
        if weather_future and t_pred_2 < lower_limit and current_price < future_price:
            return "pre-heat", f"Pre-heating: future temp {t_pred_2:.1f}°C below comfort, cheaper now (${current_price:.2f} vs ${future_price:.2f})"
        
        return "off", f"Temperature {t_pred_1:.1f}°C within comfort band ({lower_limit:.1f}°C - {upper_limit:.1f}°C)"
    
    # "Cool On" Condition: Predicted temperature exceeds upper limit
    if t_pred_1 > upper_limit:
        return "cool", f"Cooling needed: predicted {t_pred_1:.1f}°C exceeds upper limit {upper_limit:.1f}°C"
    
    # "Heat On" Condition: Predicted temperature below lower limit
    if t_pred_1 < lower_limit:
        return "heat", f"Heating needed: predicted {t_pred_1:.1f}°C below lower limit {lower_limit:.1f}°C"
    
    return "off", "Default: maintaining current state"


def generate_hvac_schedule(
    house_data: dict,
    weather_rows: List[dict],
    current_indoor_temp_c: float,
    personal_comfort: int = 5,
    target_temp_c: float = 22.0
) -> HVACSchedule:
    """
    Generate a 24-hour HVAC schedule based on weather forecast and house properties.
    
    This is the main entry point for the HVAC AI.
    """
    # Build property objects
    house = HouseProperties.from_house_data(house_data)
    hvac = HVACSystem.from_house_data(house_data)
    
    # Parse weather conditions
    weather_conditions = [WeatherConditions.from_weather_row(row) for row in weather_rows[:24]]
    
    # Pad to 24 hours if needed
    while len(weather_conditions) < 24:
        if weather_conditions:
            weather_conditions.append(weather_conditions[-1])
        else:
            weather_conditions.append(WeatherConditions(timestamp=datetime.now(), temp_outdoor_c=20))
    
    # Get comfort parameters
    lambda_comfort = LAMBDA_VALUES.get(personal_comfort, 0.5)
    
    # Deadband based on comfort preference (tighter band = more comfort)
    delta = 1.5 - (personal_comfort - 5) * 0.1  # Range: 1.0 to 2.0°C
    delta = max(0.5, min(2.0, delta))
    
    # Time-of-use pricing simulation (simplified)
    # Peak: 4PM-9PM (hours 16-21), Off-peak: other times
    def get_price(hour: int) -> float:
        if 16 <= hour <= 21:
            return ELECTRICITY_COST_KWH * 1.5  # Peak pricing
        elif 7 <= hour <= 15:
            return ELECTRICITY_COST_KWH * 1.0  # Mid-peak
        else:
            return ELECTRICITY_COST_KWH * 0.7  # Off-peak
    
    # Generate schedule
    actions = []
    current_temp = current_indoor_temp_c
    total_cost = 0.0
    total_energy = 0.0
    comfort_deviations = []
    
    for hour in range(24):
        weather = weather_conditions[hour]
        weather_next = weather_conditions[hour + 1] if hour < 23 else None
        weather_future = weather_conditions[hour + 2] if hour < 22 else None
        
        current_price = get_price(hour)
        future_price = get_price(hour + 2) if hour < 22 else current_price
        
        # Determine optimal mode
        mode, reason = determine_hvac_mode(
            house, hvac, weather, weather_next, weather_future,
            current_temp, target_temp_c, delta,
            current_price, future_price, lambda_comfort
        )
        
        # Simulate this hour
        new_temp, power_w, energy_kwh = simulate_temperature_step(
            house, hvac, weather, current_temp, target_temp_c, mode
        )
        
        # Calculate cost
        hour_cost = energy_kwh * current_price
        total_cost += hour_cost
        total_energy += energy_kwh
        
        # Track comfort deviation
        comfort_deviations.append(abs(new_temp - target_temp_c))
        
        # Create action
        start_time = f"{hour:02d}:00"
        end_time = f"{(hour + 1) % 24:02d}:00"
        
        action = HVACAction(
            hour=hour,
            mode=mode,
            start_time=start_time,
            end_time=end_time,
            power_kw=round(power_w / 1000, 2),
            cost=round(hour_cost, 2),
            reason=reason,
            predicted_temp_c=round(new_temp, 1),
            target_temp_c=target_temp_c
        )
        actions.append(action)
        
        current_temp = new_temp
    
    # Calculate comfort score (100 = perfect, decreases with deviation)
    avg_deviation = sum(comfort_deviations) / len(comfort_deviations) if comfort_deviations else 0
    comfort_score = max(0, 100 - avg_deviation * 20)
    
    return HVACSchedule(
        actions=actions,
        total_cost=total_cost,
        total_energy_kwh=total_energy,
        comfort_score=comfort_score,
        generated_at=datetime.now().isoformat()
    )


def get_current_hvac_action(schedule: HVACSchedule) -> Optional[HVACAction]:
    """Get the HVAC action for the current hour."""
    current_hour = datetime.now().hour
    for action in schedule.actions:
        if action.hour == current_hour:
            return action
    return None


def get_upcoming_actions(schedule: HVACSchedule, count: int = 5) -> List[HVACAction]:
    """Get the next N HVAC actions (excluding 'off' actions for notifications)."""
    current_hour = datetime.now().hour
    upcoming = []
    
    for action in schedule.actions:
        if action.hour >= current_hour and action.mode != "off":
            upcoming.append(action)
            if len(upcoming) >= count:
                break
    
    return upcoming


# ============================================================
# Utility Functions
# ============================================================

def celsius_to_fahrenheit(c: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return c * 9/5 + 32


def fahrenheit_to_celsius(f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (f - 32) * 5/9


def format_hvac_notification(action: HVACAction) -> str:
    """Format an HVAC action as a user-friendly notification."""
    mode_display = action.mode.replace("-", " ").title()
    return (f"HVAC will turn on {mode_display} from {action.start_time} to {action.end_time} "
            f"using {action.power_kw:.1f} kWh at ${action.cost:.2f}")


def format_schedule_summary(schedule: HVACSchedule) -> str:
    """Format the entire schedule as a summary."""
    lines = [
        f"24-Hour HVAC Schedule (Generated: {schedule.generated_at})",
        f"Total Energy: {schedule.total_energy_kwh:.2f} kWh",
        f"Total Cost: ${schedule.total_cost:.2f}",
        f"Comfort Score: {schedule.comfort_score:.0f}/100",
        "",
        "Scheduled Actions:"
    ]
    
    for action in schedule.actions:
        if action.mode != "off":
            lines.append(f"  {action.start_time}-{action.end_time}: {action.mode.upper()} "
                        f"({action.power_kw:.1f}kW, ${action.cost:.2f})")
    
    return "\n".join(lines)
