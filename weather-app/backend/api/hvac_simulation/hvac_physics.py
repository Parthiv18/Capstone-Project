"""
HVAC AI Controller - GenAI Powered
Uses Google GenAI to intelligently predict indoor temperatures and 
generate optimal HVAC schedules based on house properties, weather, and user preferences.

Provides realistic HVAC simulation with accurate:
- Power consumption (kW) based on HVAC type and house size
- Energy usage (kWh) calculations
- Cost estimates using time-of-use pricing
- Temperature predictions using thermal physics
"""

import os
import sys
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
from dotenv import load_dotenv

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import google.generativeai as genai

# ============================================================
# Configuration
# ============================================================

load_dotenv()
GENAI_KEY = os.getenv("GENAI_KEY")

if GENAI_KEY:
    genai.configure(api_key=GENAI_KEY)

# ============================================================
# Realistic HVAC Constants
# ============================================================

# Typical HVAC power consumption by type (kW)
HVAC_POWER_RATINGS = {
    "central": {"heating": 10.0, "cooling": 3.5},      # Central AC/Furnace
    "heat_pump": {"heating": 3.0, "cooling": 3.0},     # Heat pump (efficient)
    "mini_split": {"heating": 1.5, "cooling": 1.2},    # Mini-split per zone
    "window_ac": {"heating": 1.5, "cooling": 1.0},     # Window unit
    "none": {"heating": 0, "cooling": 0},
}

# Scaling factor for house size (base is 1500 sqft)
def get_hvac_capacity(hvac_type: str, house_size_sqft: float, mode: str) -> float:
    """Get HVAC power capacity in kW based on type and house size."""
    base_power = HVAC_POWER_RATINGS.get(hvac_type, HVAC_POWER_RATINGS["central"])
    power = base_power.get(mode, 3.0)
    # Scale by house size (larger houses need more power)
    scale = max(0.5, min(2.0, house_size_sqft / 1500))
    return round(power * scale, 2)

# Time-of-use electricity pricing ($/kWh)
def get_electricity_price(hour: int) -> float:
    """Get electricity price based on time of day."""
    if 22 <= hour or hour < 6:  # Off-peak: 10PM-6AM
        return 0.08
    elif 16 <= hour < 21:  # Peak: 4PM-9PM
        return 0.20
    else:  # Mid-peak: 6AM-4PM, 9PM-10PM
        return 0.12

# ============================================================
# Data Classes
# ============================================================

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
    """Complete HVAC schedule for 24 hours."""
    actions: List[HVACAction]
    total_cost: float
    total_energy_kwh: float
    comfort_score: float
    generated_at: str
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "actions": [asdict(a) for a in self.actions],
            "total_cost": self.total_cost,
            "total_energy_kwh": self.total_energy_kwh,
            "comfort_score": self.comfort_score,
            "generated_at": self.generated_at
        }


# ============================================================
# Realistic Thermal Model Reference
# ============================================================

THERMAL_MODEL_REFERENCE = """
## Realistic Thermal Physics Model

### Building Heat Balance Equation
Q_total = Q_envelope + Q_infiltration + Q_solar + Q_internal + Q_hvac

### 1. Envelope Heat Loss/Gain (through walls, roof, windows)
Q_envelope = U_total × A_total × (T_outdoor - T_indoor)

U-values by insulation quality (W/m²K):
- Excellent insulation: U = 0.3 (well-insulated modern home)
- Average insulation: U = 0.8 (typical home)
- Poor insulation: U = 1.5 (old/uninsulated home)

Typical surface areas for house sizes:
- 1000 sqft house: ~250 m² total envelope
- 1500 sqft house: ~320 m² total envelope
- 2500 sqft house: ~450 m² total envelope

### 2. Air Infiltration Heat Loss
Q_infiltration = 0.33 × ACH × V × (T_outdoor - T_indoor)

Air Changes per Hour (ACH):
- Excellent (tight): 0.2 ACH
- Average: 0.5 ACH
- Poor (leaky): 1.0 ACH

### 3. Solar Heat Gain
Q_solar = SHGC × A_windows × I_solar × 0.5 (factor for orientation)
- SHGC typical: 0.25-0.6
- Window area: ~15% of floor area

### 4. HVAC Capacity (Realistic ranges)
Heating capacity by system:
- Central furnace: 8-15 kW (scaled by house size)
- Heat pump: 2-5 kW
- Mini-split: 1-3 kW per head
- Space heater: 1-2 kW

Cooling capacity:
- Central AC: 3-7 kW (1 ton = 3.5 kW)
- Heat pump: 2-5 kW
- Mini-split: 1-2 kW
- Window AC: 0.5-1.5 kW

### 5. Temperature Change Rate
For a typical house:
- Thermal mass (C): 50,000 - 150,000 kJ/°C depending on construction
- Temperature change: ΔT = (Q_total × Δt) / C

Realistic expectations for 5-minute timestep:
- HVAC heating: +0.1 to +0.4°C 
- HVAC cooling: -0.1 to -0.3°C
- Natural drift (HVAC off): -0.02 to +0.02°C typically
- Extreme conditions: up to ±0.1°C drift

### 6. COP (Coefficient of Performance)
Heating:
- Heat pump: COP 2.5-4.0 (varies with outdoor temp)
- Electric furnace: COP 1.0
- Gas furnace: efficiency 80-95%

Cooling:
- Central AC: COP 3.0-4.5
- Heat pump: COP 3.0-4.5
- Window AC: COP 2.5-3.5
"""

# ============================================================
# GenAI Prompt Builders
# ============================================================

def build_realtime_simulation_prompt(
    house_data: Dict[str, Any],
    current_indoor_temp_c: float,
    target_temp_c: float,
    weather_data: Dict[str, Any],
    hvac_schedule: Optional[Dict] = None,
) -> str:
    """Build a realistic prompt for real-time HVAC simulation."""
    
    current_hour = datetime.now().hour
    current_minute = datetime.now().minute
    
    # Get HVAC specs
    hvac_type = house_data.get('hvac_type', 'central')
    house_size = float(house_data.get('home_size', 1500))
    insulation = house_data.get('insulation_quality', 'average')
    house_age = int(house_data.get('age_of_house', 20))
    
    # Calculate realistic HVAC capacities
    heating_capacity = get_hvac_capacity(hvac_type, house_size, "heating")
    cooling_capacity = get_hvac_capacity(hvac_type, house_size, "cooling")
    
    # Get scheduled mode if available
    scheduled_mode = "off"
    if hvac_schedule and "actions" in hvac_schedule:
        for action in hvac_schedule["actions"]:
            if action.get("hour") == current_hour:
                scheduled_mode = action.get("mode", "off")
                break
    
    # Weather data
    outdoor_temp = weather_data.get('temperature_2m', 20)
    humidity = weather_data.get('relative_humidity_2m', 50)
    wind_speed = weather_data.get('wind_speed_10m', 0)
    solar = weather_data.get('shortwave_radiation', 0)
    
    # Temperature difference for context
    temp_diff = target_temp_c - current_indoor_temp_c
    
    prompt = f"""You are a realistic HVAC simulation AI. Simulate exactly what happens in the next 5 minutes.

{THERMAL_MODEL_REFERENCE}

## House Specifications
- Size: {house_size} sqft
- Age: {house_age} years (affects insulation degradation)
- Insulation Quality: {insulation}
- HVAC Type: {hvac_type}
- HVAC Heating Capacity: {heating_capacity} kW
- HVAC Cooling Capacity: {cooling_capacity} kW

## Current Conditions
- Time: {current_hour:02d}:{current_minute:02d}
- Indoor Temperature: {current_indoor_temp_c:.1f}°C
- Target Temperature: {target_temp_c:.1f}°C
- Temperature Gap: {temp_diff:+.1f}°C ({"needs heating" if temp_diff > 0.3 else "needs cooling" if temp_diff < -0.3 else "at target"})
- Outdoor Temperature: {outdoor_temp}°C
- Outdoor Humidity: {humidity}%
- Wind Speed: {wind_speed} m/s
- Solar Radiation: {solar} W/m²
- Scheduled HVAC Mode: {scheduled_mode}

## HVAC Control Logic
Apply these realistic rules:
1. DEADBAND: Only turn ON if temp is more than 0.5°C away from target
2. TURN OFF: When within 0.3°C of target to prevent overshooting
3. REALISTIC RATES: 
   - Heating raises temp ~0.1-0.3°C per 5 minutes depending on capacity
   - Cooling lowers temp ~0.1-0.2°C per 5 minutes
   - Natural drift is very slow: ±0.01-0.05°C per 5 minutes
4. NEVER exceed target by more than 0.3°C (overshoot protection)

## Energy Calculation
- If heating: Power = {heating_capacity} kW
- If cooling: Power = {cooling_capacity} kW
- Energy for 5 min: kWh = power_kw × (5/60)
- Current electricity rate: ${get_electricity_price(current_hour)}/kWh

## Your Task
Determine:
1. Should HVAC run? (based on deadband logic)
2. What will indoor temperature be in 5 minutes?
3. How much energy will be used?

## CRITICAL CONSTRAINTS
- Temperature MUST stay realistic (between 10°C and 35°C indoor)
- Temperature change in 5 minutes should be SMALL (max ±0.5°C)
- If HVAC is off, temp drifts slowly toward outdoor temp
- Power consumption must match the HVAC type specified

## Output (JSON only, no markdown):
{{
    "new_temp_c": {round(current_indoor_temp_c, 1)},
    "hvac_mode": "off",
    "hvac_power_kw": 0.0,
    "hvac_energy_kwh": 0.0,
    "electricity_rate": {get_electricity_price(current_hour)},
    "cost_this_step": 0.0,
    "should_run_hvac": false,
    "reason": "Explanation of decision"
}}
"""
    return prompt


def build_schedule_prompt(
    house_data: Dict[str, Any],
    weather_forecast: List[Dict],
    current_indoor_temp_c: float,
    target_temp_c: float,
) -> str:
    """Build prompt for realistic 24-hour HVAC schedule generation starting from current hour."""
    
    # Get current time
    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute
    
    # House specs
    hvac_type = house_data.get('hvac_type', 'central')
    house_size = float(house_data.get('home_size', 1500))
    insulation = house_data.get('insulation_quality', 'average')
    house_age = int(house_data.get('age_of_house', 20))
    
    # HVAC capacities
    heating_capacity = get_hvac_capacity(hvac_type, house_size, "heating")
    cooling_capacity = get_hvac_capacity(hvac_type, house_size, "cooling")
    
    # Format weather forecast starting from current hour for next 24 hours
    weather_lines = []
    for i in range(24):
        # Calculate the actual hour (wrapping around midnight)
        actual_hour = (current_hour + i) % 24
        # Get weather for this hour (use modulo to wrap around forecast data)
        weather_idx = min(i, len(weather_forecast) - 1)
        row = weather_forecast[weather_idx] if weather_idx < len(weather_forecast) else weather_forecast[-1]
        temp = row.get("temperature_2m", 20)
        solar = row.get("shortwave_radiation", 0)
        price = get_electricity_price(actual_hour)
        
        # Show relative time for clarity
        if i == 0:
            time_label = "NOW"
        elif i == 1:
            time_label = "in 1 hour"
        else:
            time_label = f"in {i} hours"
        
        weather_lines.append(f"  {actual_hour:02d}:00 ({time_label}): {temp}°C outdoor, {solar}W/m² solar, ${price}/kWh")
    
    prompt = f"""You are an HVAC scheduling AI. Create a realistic 24-hour schedule starting from NOW to maintain comfort while minimizing cost.

{THERMAL_MODEL_REFERENCE}

## CURRENT TIME: {current_hour:02d}:{current_minute:02d}

## House Specifications  
- Size: {house_size} sqft
- Age: {house_age} years
- Insulation: {insulation}
- HVAC Type: {hvac_type}
- Heating Capacity: {heating_capacity} kW
- Cooling Capacity: {cooling_capacity} kW

## User Settings
- Target Temperature: {target_temp_c}°C
- Acceptable Range: {target_temp_c - 1.0}°C to {target_temp_c + 1.0}°C
- Current Indoor Temp: {current_indoor_temp_c}°C

## Weather Forecast & Electricity Prices (Next 24 Hours Starting NOW)
{chr(10).join(weather_lines)}

## Scheduling Strategy
1. PRE-CONDITION during off-peak hours ($0.08/kWh, 10PM-6AM) when beneficial
2. COAST through peak hours ($0.20/kWh, 4PM-9PM) using thermal mass
3. AVOID running during peak unless necessary for comfort
4. Consider that temperature drifts slowly - house has thermal mass
5. Start schedule from CURRENT HOUR ({current_hour:02d}:00), not from midnight

## Energy & Cost Calculations
For each hour where HVAC runs:
- Heating: power_kw = {heating_capacity}, energy = {heating_capacity} kWh, cost = {heating_capacity} × rate
- Cooling: power_kw = {cooling_capacity}, energy = {cooling_capacity} kWh, cost = {cooling_capacity} × rate
- Off: power_kw = 0, energy = 0, cost = 0

## CRITICAL: Generate schedule starting from hour {current_hour} (current time)
The first action MUST be for hour {current_hour} (now).
Then continue for the next 23 hours, wrapping around midnight if needed.

## Output Format (JSON only, no markdown):
{{
    "actions": [
        {{
            "hour": {current_hour},
            "mode": "off|heat|cool|pre-heat|pre-cool",
            "start_time": "{current_hour:02d}:00",
            "end_time": "{(current_hour+1)%24:02d}:00", 
            "power_kw": 0.0,
            "cost": 0.0,
            "reason": "Why this decision for RIGHT NOW",
            "predicted_temp_c": {current_indoor_temp_c},
            "target_temp_c": {target_temp_c}
        }},
        ... (continue for next 23 hours)
    ],
    "total_cost": 0.0,
    "total_energy_kwh": 0.0,
    "comfort_score": 95.0,
    "strategy_summary": "Brief optimization strategy"
}}

Generate exactly 24 actions starting from hour {current_hour}. Be REALISTIC with power/energy values matching the HVAC specs above.
"""
    return prompt


# ============================================================
# GenAI API Calls
# ============================================================

def call_genai(prompt: str) -> Optional[Dict]:
    """Call GenAI and parse JSON response."""
    if not GENAI_KEY:
        print("Warning: GENAI_KEY not configured")
        return None
    
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.1,  # Low for consistent calculations
                max_output_tokens=4096,
                response_mime_type="application/json",
            )
        )
        
        response_text = response.text.strip()
        
        # Clean up response
        if "```" in response_text:
            response_text = re.sub(r'^```(?:json)?\s*\n?', '', response_text)
            response_text = re.sub(r'\n?```\s*$', '', response_text)
            response_text = response_text.strip()
        
        # Parse JSON with fallbacks
        result = None
        
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON object
            first_brace = response_text.find('{')
            last_brace = response_text.rfind('}')
            if first_brace != -1 and last_brace > first_brace:
                try:
                    result = json.loads(response_text[first_brace:last_brace + 1])
                except json.JSONDecodeError:
                    pass
        
        # Fix trailing commas
        if result is None:
            fixed = re.sub(r',\s*([}\]])', r'\1', response_text)
            try:
                result = json.loads(fixed)
            except json.JSONDecodeError:
                pass
        
        return result
        
    except Exception as e:
        print(f"GenAI API error: {e}")
        return None


# ============================================================
# Main Simulation Functions
# ============================================================

def simulate_step_with_hvac(
    house_data: Dict[str, Any],
    current_indoor_temp_c: float,
    target_temp_c: float,
    weather_data: Dict[str, Any],
    hvac_schedule: Optional[Dict] = None,
    personal_comfort: float = 22.0  # Now this is the actual target temp
) -> Dict[str, Any]:
    """
    Simulate one 5-minute timestep with realistic HVAC control.
    """
    # Validate inputs - keep temperature realistic
    current_indoor_temp_c = max(5.0, min(40.0, float(current_indoor_temp_c)))
    target_temp_c = max(15.0, min(30.0, float(target_temp_c)))
    
    prompt = build_realtime_simulation_prompt(
        house_data=house_data,
        current_indoor_temp_c=current_indoor_temp_c,
        target_temp_c=target_temp_c,
        weather_data=weather_data,
        hvac_schedule=hvac_schedule,
    )
    
    result = call_genai(prompt)
    
    if result is None:
        # Fallback with realistic physics
        return _fallback_simulation(
            house_data=house_data,
            current_indoor_temp_c=current_indoor_temp_c,
            target_temp_c=target_temp_c,
            weather_data=weather_data,
        )
    
    # Validate and constrain the result
    new_temp = float(result.get("new_temp_c", current_indoor_temp_c))
    
    # Constrain temperature change to realistic values
    max_change = 0.5  # Max 0.5°C change in 5 minutes
    temp_change = new_temp - current_indoor_temp_c
    if abs(temp_change) > max_change:
        new_temp = current_indoor_temp_c + (max_change if temp_change > 0 else -max_change)
    
    # Keep temperature in realistic range
    new_temp = max(10.0, min(35.0, new_temp))
    
    result["new_temp_c"] = round(new_temp, 2)
    
    return result


def _fallback_simulation(
    house_data: Dict[str, Any],
    current_indoor_temp_c: float,
    target_temp_c: float,
    weather_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Fallback simulation when GenAI is unavailable."""
    outdoor_temp = float(weather_data.get("temperature_2m", 20))
    hvac_type = house_data.get('hvac_type', 'central')
    house_size = float(house_data.get('home_size', 1500))
    insulation = house_data.get('insulation_quality', 'average')
    
    # Insulation affects drift rate
    drift_rates = {"excellent": 0.005, "average": 0.015, "poor": 0.03}
    drift_rate = drift_rates.get(insulation, 0.015)
    
    # Natural temperature drift toward outdoor
    natural_drift = (outdoor_temp - current_indoor_temp_c) * drift_rate
    
    # Determine HVAC mode based on deadband
    temp_diff = target_temp_c - current_indoor_temp_c
    hvac_mode = "off"
    power_kw = 0
    hvac_effect = 0
    
    if temp_diff > 0.5:  # Need heating
        hvac_mode = "heating"
        power_kw = get_hvac_capacity(hvac_type, house_size, "heating")
        hvac_effect = 0.2  # Heat by ~0.2°C
    elif temp_diff < -0.5:  # Need cooling
        hvac_mode = "cooling"
        power_kw = get_hvac_capacity(hvac_type, house_size, "cooling")
        hvac_effect = -0.15  # Cool by ~0.15°C
    
    # Calculate new temperature
    new_temp = current_indoor_temp_c + natural_drift + hvac_effect
    new_temp = max(10.0, min(35.0, new_temp))  # Keep realistic
    
    # Energy and cost
    current_hour = datetime.now().hour
    energy_kwh = power_kw * (5 / 60)  # 5 minutes
    rate = get_electricity_price(current_hour)
    cost = energy_kwh * rate
    
    return {
        "new_temp_c": round(new_temp, 2),
        "hvac_mode": hvac_mode,
        "hvac_power_kw": round(power_kw, 2),
        "hvac_energy_kwh": round(energy_kwh, 4),
        "electricity_rate": rate,
        "cost_this_step": round(cost, 4),
        "should_run_hvac": hvac_mode != "off",
        "reason": f"Fallback: {'Heating needed' if hvac_mode == 'heating' else 'Cooling needed' if hvac_mode == 'cooling' else 'At target temperature'}"
    }


def generate_hvac_schedule(
    house_data: Dict[str, Any],
    weather_rows: List[Dict],
    current_indoor_temp_c: float,
    personal_comfort: float = 22.0,  # This is the target temp directly
    target_temp_c: float = 22.0
) -> HVACSchedule:
    """Generate an optimized 24-hour HVAC schedule."""
    
    # Use the target_temp_c (which should be validated/set from personal_comfort)
    target = max(15.0, min(30.0, float(target_temp_c)))
    
    prompt = build_schedule_prompt(
        house_data=house_data,
        weather_forecast=weather_rows,
        current_indoor_temp_c=current_indoor_temp_c,
        target_temp_c=target,
    )
    
    result = call_genai(prompt)
    
    if result is None or "actions" not in result:
        return _generate_fallback_schedule(
            house_data=house_data,
            current_indoor_temp_c=current_indoor_temp_c,
            target_temp_c=target,
            weather_rows=weather_rows,
        )
    
    # Parse actions
    actions = []
    total_cost = 0
    total_energy = 0
    
    for action_data in result.get("actions", []):
        power = float(action_data.get("power_kw", 0))
        cost = float(action_data.get("cost", 0))
        total_cost += cost
        total_energy += power  # Power for 1 hour = energy in kWh
        
        actions.append(HVACAction(
            hour=int(action_data.get("hour", 0)),
            mode=action_data.get("mode", "off"),
            start_time=action_data.get("start_time", "00:00"),
            end_time=action_data.get("end_time", "01:00"),
            power_kw=power,
            cost=cost,
            reason=action_data.get("reason", ""),
            predicted_temp_c=float(action_data.get("predicted_temp_c", target)),
            target_temp_c=target
        ))
    
    # Ensure 24 hours
    while len(actions) < 24:
        hour = len(actions)
        actions.append(HVACAction(
            hour=hour,
            mode="off",
            start_time=f"{hour:02d}:00",
            end_time=f"{(hour+1)%24:02d}:00",
            power_kw=0,
            cost=0,
            reason="No action scheduled",
            predicted_temp_c=target,
            target_temp_c=target
        ))
    
    return HVACSchedule(
        actions=actions[:24],
        total_cost=round(result.get("total_cost", total_cost), 2),
        total_energy_kwh=round(result.get("total_energy_kwh", total_energy), 2),
        comfort_score=float(result.get("comfort_score", 85)),
        generated_at=datetime.now().isoformat()
    )


def _generate_fallback_schedule(
    house_data: Dict[str, Any],
    current_indoor_temp_c: float,
    target_temp_c: float,
    weather_rows: List[Dict]
) -> HVACSchedule:
    """Generate fallback schedule when GenAI unavailable."""
    hvac_type = house_data.get('hvac_type', 'central')
    house_size = float(house_data.get('home_size', 1500))
    
    actions = []
    total_cost = 0
    total_energy = 0
    predicted_temp = current_indoor_temp_c
    
    for hour in range(24):
        weather = weather_rows[hour] if hour < len(weather_rows) else weather_rows[-1]
        outdoor_temp = float(weather.get("temperature_2m", 20))
        
        # Determine mode
        mode = "off"
        power = 0
        
        temp_diff = target_temp_c - predicted_temp
        
        if temp_diff > 1.0 or outdoor_temp < target_temp_c - 5:
            mode = "heat"
            power = get_hvac_capacity(hvac_type, house_size, "heating")
            predicted_temp = min(target_temp_c + 0.5, predicted_temp + 1.0)
        elif temp_diff < -1.0 or outdoor_temp > target_temp_c + 5:
            mode = "cool"
            power = get_hvac_capacity(hvac_type, house_size, "cooling")
            predicted_temp = max(target_temp_c - 0.5, predicted_temp - 0.8)
        else:
            # Natural drift
            drift = (outdoor_temp - predicted_temp) * 0.1
            predicted_temp += drift
        
        price = get_electricity_price(hour)
        cost = power * price
        total_cost += cost
        total_energy += power
        
        actions.append(HVACAction(
            hour=hour,
            mode=mode,
            start_time=f"{hour:02d}:00",
            end_time=f"{(hour+1)%24:02d}:00",
            power_kw=power,
            cost=round(cost, 2),
            reason=f"Fallback: {'Heating' if mode == 'heat' else 'Cooling' if mode == 'cool' else 'Maintaining'} @ ${price}/kWh",
            predicted_temp_c=round(predicted_temp, 1),
            target_temp_c=target_temp_c
        ))
    
    return HVACSchedule(
        actions=actions,
        total_cost=round(total_cost, 2),
        total_energy_kwh=round(total_energy, 2),
        comfort_score=75,
        generated_at=datetime.now().isoformat()
    )


def predict_temperature(
    house_data: Dict[str, Any],
    current_indoor_temp_c: float,
    weather_data: Dict[str, Any],
    hvac_mode: str = "off",
    target_temp_c: float = 22.0,
    timestep_minutes: int = 5
) -> Dict[str, Any]:
    """Predict temperature (wrapper for simulate_step_with_hvac)."""
    return simulate_step_with_hvac(
        house_data=house_data,
        current_indoor_temp_c=current_indoor_temp_c,
        target_temp_c=target_temp_c,
        weather_data=weather_data,
        hvac_schedule=None,
    )


# ============================================================
# Helper Functions
# ============================================================

def get_current_hvac_action(schedule: HVACSchedule) -> Optional[HVACAction]:
    """Get HVAC action for current hour."""
    current_hour = datetime.now().hour
    for action in schedule.actions:
        if action.hour == current_hour:
            return action
    return None


def get_upcoming_actions(schedule: HVACSchedule, count: int = 5) -> List[HVACAction]:
    """Get next N non-off HVAC actions (present and future only)."""
    current_hour = datetime.now().hour
    upcoming = []
    
    # Sort actions by how far in the future they are from current hour
    def hours_until(action_hour):
        diff = action_hour - current_hour
        if diff < 0:
            diff += 24  # Wrap around midnight
        return diff
    
    # Filter and sort actions
    sorted_actions = sorted(schedule.actions, key=lambda a: hours_until(a.hour))
    
    for action in sorted_actions:
        hours_away = hours_until(action.hour)
        # Only include present (0 hours away) and future (up to 12 hours ahead)
        if hours_away <= 12 and action.mode != "off":
            upcoming.append(action)
            if len(upcoming) >= count:
                break
    
    return upcoming


def celsius_to_fahrenheit(c: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return c * 9/5 + 32


def fahrenheit_to_celsius(f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (f - 32) * 5/9
