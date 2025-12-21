"""
Appliance Alerts Module
Uses Google GenAI to generate optimal appliance schedules based on HVAC data.
Helps users save energy and costs by running appliances at optimal times.
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from dotenv import load_dotenv

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import google.generativeai as genai
from database.db import (
    get_user_state,
    get_user_id,
    set_appliance_alerts,
    get_appliance_alerts,
)

# ============================================================
# Configuration
# ============================================================

load_dotenv()
GENAI_KEY = os.getenv("GENAI_KEY")

if GENAI_KEY:
    genai.configure(api_key=GENAI_KEY)

router = APIRouter()

# ============================================================
# Appliance Power Consumption Data (kW)
# Maps exact appliance names from house form to power consumption
# ============================================================

APPLIANCE_POWER_KW = {
    "Electric Space Heater": 1.5,
    "Portable Air Conditioner": 1.2,
    "Electric Water Heater": 4.5,
    "Gas Water Heater": 0.3,  # Just the igniter/controls
    "Oven (Electric or Gas)": 2.5,
    "Stove / Cooktop (Electric, Gas, or Induction)": 2.0,
    "Clothes Dryer (Electric or Gas)": 3.0,
    "Washing Machine (hot water cycles)": 0.5,
    "Dishwasher (especially drying cycles)": 1.8,
    "Electric Vehicle Charger (Level 1 or Level 2)": 7.2,  # Level 2 charger
}

# Typical run times in minutes
APPLIANCE_RUN_TIMES = {
    "Electric Space Heater": 120,  # 2 hours typical use
    "Portable Air Conditioner": 180,  # 3 hours typical use
    "Electric Water Heater": 30,
    "Gas Water Heater": 30,
    "Oven (Electric or Gas)": 60,
    "Stove / Cooktop (Electric, Gas, or Induction)": 45,
    "Clothes Dryer (Electric or Gas)": 60,
    "Washing Machine (hot water cycles)": 45,
    "Dishwasher (especially drying cycles)": 90,
    "Electric Vehicle Charger (Level 1 or Level 2)": 480,  # 8 hours for full charge
}

# Heat generation impact on indoor temperature (True = generates significant heat)
APPLIANCE_HEAT_GENERATORS = {
    "Electric Space Heater": True,
    "Portable Air Conditioner": False,  # Actually cools
    "Electric Water Heater": False,
    "Gas Water Heater": False,
    "Oven (Electric or Gas)": True,
    "Stove / Cooktop (Electric, Gas, or Induction)": True,
    "Clothes Dryer (Electric or Gas)": True,
    "Washing Machine (hot water cycles)": False,
    "Dishwasher (especially drying cycles)": True,
    "Electric Vehicle Charger (Level 1 or Level 2)": False,
}


# ============================================================
# GenAI Prompt Builder
# ============================================================

def build_genai_prompt(
    appliances: List[str],
    hvac_schedule: Dict[str, Any],
    weather_data: List[Dict],
    house_data: Dict[str, Any],
    current_temp_c: float,
    target_temp_c: float,
) -> str:
    """Build a comprehensive prompt for GenAI to generate appliance schedules."""
    
    # Format HVAC schedule info
    hvac_actions = hvac_schedule.get("actions", [])
    hvac_summary = []
    total_hvac_energy = hvac_schedule.get("total_energy_kwh", 0)
    
    for action in hvac_actions[:12]:  # Show first 12 hours
        mode = action.get("mode", "off")
        hour = action.get("hour", 0)
        power = action.get("power_kw", 0)
        hvac_summary.append(f"Hour {hour:02d}:00 - Mode: {mode}, Power: {power:.2f} kW")
    
    # Format weather forecast
    weather_summary = []
    for i, row in enumerate(weather_data[:12]):  # First 12 hours
        temp = row.get("temperature_2m", 20)
        humidity = row.get("relative_humidity_2m", 50)
        time_str = row.get("time", f"Hour {i}")
        weather_summary.append(f"{time_str}: {temp}°C, {humidity}% humidity")
    
    # Get appliance power info - use exact appliance names from house form
    appliance_info = []
    for app in appliances:
        # Look up directly using the exact name from house form
        power = APPLIANCE_POWER_KW.get(app, 1.0)
        run_time = APPLIANCE_RUN_TIMES.get(app, 30)
        is_heat_generator = APPLIANCE_HEAT_GENERATORS.get(app, False)
        heat_note = " (generates heat)" if is_heat_generator else ""
        appliance_info.append(f"- {app}: {power} kW, typical run time: {run_time} minutes{heat_note}")
    
    prompt = f"""You are an energy optimization AI assistant. Analyze the following data and generate optimal appliance schedules to minimize energy costs and reduce HVAC load.

## House Information
- Size: {house_data.get('home_size', 1500)} sqft
- Insulation: {house_data.get('insulation_quality', 'average')}
- HVAC Type: {house_data.get('hvac_type', 'central_ac')}
- Current Indoor Temperature: {current_temp_c:.1f}°C
- Target Temperature: {target_temp_c:.1f}°C

## HVAC Schedule (Next 12 Hours)
Total HVAC Energy: {total_hvac_energy:.2f} kWh
{chr(10).join(hvac_summary)}

## Weather Forecast
{chr(10).join(weather_summary)}

## User's Appliances
{chr(10).join(appliance_info)}

## Task
For each appliance, determine:
1. The BEST time to run it (avoid HVAC peak hours)
2. Duration of operation
3. Energy cost estimate (assume $0.15/kWh)
4. Reason why this time is optimal

## Important Optimization Rules
- Heat-generating appliances (dryer, oven, dishwasher) should NOT run during cooling periods
- Run high-power appliances when HVAC is OFF or at low power
- Prefer running appliances during milder outdoor temperatures
- Consider appliance heat output impact on indoor temperature
- Stagger high-power appliances to avoid peak demand

## Required Output Format
Respond ONLY with valid JSON in this exact format (no markdown, no explanation outside JSON):
{{
    "appliance_schedules": [
        {{
            "appliance": "appliance_name",
            "optimal_start_time": "HH:MM",
            "optimal_end_time": "HH:MM", 
            "duration_minutes": 60,
            "power_kw": 3.0,
            "estimated_cost": 0.45,
            "priority": "high/medium/low",
            "reason": "Brief explanation why this time is optimal",
            "alert_message": "User-friendly alert message"
        }}
    ],
    "daily_summary": {{
        "total_appliance_energy_kwh": 0.0,
        "total_appliance_cost": 0.0,
        "estimated_savings_percent": 0,
        "peak_avoidance_hours": ["HH:MM-HH:MM"],
        "tip": "General energy saving tip for today"
    }},
    "alerts": [
        {{
            "type": "warning/info/success",
            "message": "Alert message for user",
            "appliance": "related_appliance_or_general"
        }}
    ]
}}
"""
    return prompt


# ============================================================
# GenAI Integration
# ============================================================

def generate_appliance_alerts(username: str, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Generate appliance usage alerts using GenAI based on HVAC schedule and user data.
    
    Args:
        username: User's username
        force_refresh: If True, regenerate even if recent alerts exist
        
    Returns:
        Dict containing appliance schedules and alerts
    """
    if not GENAI_KEY:
        return {"error": "GenAI API key not configured"}
    
    # Get user state
    state = get_user_state(username)
    if state is None:
        return {"error": "User not found"}
    
    user_id = state.get("id")
    if user_id is None:
        return {"error": "User ID missing"}
    
    # Check for cached alerts (unless force refresh)
    if not force_refresh:
        cached_alerts = state.get("appliance_alerts")
        if cached_alerts:
            # Check if alerts are from today
            generated_date = cached_alerts.get("generated_date", "")
            today = datetime.now().strftime("%Y-%m-%d")
            if generated_date == today:
                return cached_alerts
    
    # Get house data and appliances
    house = state.get("house")
    if not house:
        return {"error": "House data missing - please submit house information first"}
    
    house_data = house.get("data", house) if isinstance(house, dict) else house
    appliances = house.get("appliances", [])
    
    if not appliances:
        return {
            "error": "No appliances found",
            "message": "Please add appliances in your house settings to get personalized alerts"
        }
    
    # Get HVAC schedule
    hvac_sim = state.get("hvac_sim")
    if not hvac_sim:
        return {"error": "HVAC schedule missing - please run HVAC simulation first"}
    
    # Get weather data
    raw_weather = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing"}
    
    # Parse weather rows
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
        return {"error": "Weather data rows missing"}
    
    # Get current and target temperatures
    current_temp = state.get("simulated_temp", 22.0)
    target_temp = state.get("target_setpoint", 22.0)
    
    # Build the prompt
    prompt = build_genai_prompt(
        appliances=appliances,
        hvac_schedule=hvac_sim,
        weather_data=weather_rows,
        house_data=house_data,
        current_temp_c=float(current_temp),
        target_temp_c=float(target_temp),
    )
    
    try:
        # Call GenAI
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.2,  # Lower temperature for more consistent output
                max_output_tokens=2048,
                response_mime_type="application/json",  # Request JSON response
            )
        )
        
        # Parse the response
        response_text = response.text.strip()
        
        # Clean up response - remove markdown code blocks if present
        if "```" in response_text:
            # Remove all code block markers
            import re
            # Match ```json or ``` at start and ``` at end
            response_text = re.sub(r'^```(?:json)?\s*\n?', '', response_text)
            response_text = re.sub(r'\n?```\s*$', '', response_text)
            response_text = response_text.strip()
        
        # Parse JSON with multiple fallback strategies
        result = None
        parse_error = None
        
        # Strategy 1: Direct parse
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError as e:
            parse_error = str(e)
        
        # Strategy 2: Find outermost JSON object
        if result is None:
            import re
            # Find the first { and last } to extract JSON
            first_brace = response_text.find('{')
            last_brace = response_text.rfind('}')
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                json_str = response_text[first_brace:last_brace + 1]
                try:
                    result = json.loads(json_str)
                except json.JSONDecodeError:
                    pass
        
        # Strategy 3: Try to fix common JSON issues
        if result is None:
            # Remove trailing commas before } or ]
            import re
            fixed_text = re.sub(r',\s*([}\]])', r'\1', response_text)
            try:
                result = json.loads(fixed_text)
            except json.JSONDecodeError:
                pass
        
        # If all parsing failed, return error with debug info
        if result is None:
            print(f"GenAI Response parsing failed. Raw response:\n{response_text[:1000]}")
            return {
                "error": "Failed to parse GenAI response",
                "message": "The AI returned an invalid response format. Please try again.",
                "debug_info": parse_error
            }
        
        # Validate expected structure
        if "appliance_schedules" not in result:
            result["appliance_schedules"] = []
        if "daily_summary" not in result:
            result["daily_summary"] = {
                "total_appliance_energy_kwh": 0,
                "total_appliance_cost": 0,
                "estimated_savings_percent": 0,
                "peak_avoidance_hours": [],
                "tip": "Run high-power appliances during off-peak hours"
            }
        if "alerts" not in result:
            result["alerts"] = []
        
        # Add metadata
        result["generated_date"] = datetime.now().strftime("%Y-%m-%d")
        result["generated_time"] = datetime.now().strftime("%H:%M:%S")
        result["username"] = username
        result["appliances_analyzed"] = appliances
        
        # Store in database
        set_appliance_alerts(user_id, result)
        
        return result
        
    except Exception as e:
        return {
            "error": f"GenAI API error: {str(e)}",
            "message": "Failed to generate appliance alerts"
        }


# ============================================================
# API Endpoints
# ============================================================

@router.get("/alerts/{username}")
def get_alerts(username: str, refresh: bool = False):
    """
    Get appliance alerts and schedules for a user.
    
    Query params:
        refresh: If true, force regenerate alerts even if cached
    """
    try:
        result = generate_appliance_alerts(username, force_refresh=refresh)
        if "error" in result:
            # Return partial result with error info
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"Alerts Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts/{username}/refresh")
def refresh_alerts(username: str):
    """Force regenerate appliance alerts for a user."""
    try:
        result = generate_appliance_alerts(username, force_refresh=True)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"Alerts Refresh Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts/{username}/cached")
def get_cached_alerts(username: str):
    """Get only cached alerts without regenerating."""
    try:
        user_id = get_user_id(username)
        if not user_id:
            raise HTTPException(status_code=404, detail="User not found")
        
        alerts = get_appliance_alerts(user_id)
        if not alerts:
            raise HTTPException(
                status_code=404, 
                detail="No cached alerts found - call /alerts/{username} to generate"
            )
        return alerts
    except HTTPException:
        raise
    except Exception as e:
        print(f"Cached Alerts Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
