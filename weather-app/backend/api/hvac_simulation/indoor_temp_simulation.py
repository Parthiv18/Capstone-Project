"""
Indoor Temperature Simulation Module - GenAI Powered
Uses Google GenAI to predict indoor temperature and control HVAC.
Integrates with database for state management.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

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
    simulate_step_with_hvac,
    predict_temperature,
    celsius_to_fahrenheit,
    HVACSchedule
)

# ============================================================
# Constants
# ============================================================

DEFAULT_TIMESTEP_MINUTES = 5

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


def _extract_weather_rows(raw_weather: Any) -> List[Dict]:
    """Extract weather rows from various data formats."""
    if isinstance(raw_weather, dict):
        return raw_weather.get("rows", [])
    elif isinstance(raw_weather, list):
        if raw_weather and isinstance(raw_weather[0], dict):
            if "data" in raw_weather[0]:
                # List of snapshots with dates
                latest_snapshot = raw_weather[-1]
                weather_data = latest_snapshot.get("data", {})
                return weather_data.get("rows", []) if isinstance(weather_data, dict) else []
            else:
                return raw_weather
    return []


def _get_target_temperature(
    user_id: int,
    state: Dict,
    house_data: Dict,
    explicit_target: Optional[float] = None
) -> float:
    """
    Determine target temperature with priority:
    1) Explicitly passed value (user adjusted setpoint via thermostat)
    2) Saved setpoint in database
    3) Use personal_comfort from house data (this IS the target temp in °C)
    
    All values are validated to be within realistic HVAC range (15-30°C).
    """
    # Helper to validate and clamp temperature
    def validate_temp(temp: float) -> float:
        return max(15.0, min(30.0, float(temp)))
    
    if explicit_target is not None:
        # User explicitly set a new target via thermostat - validate and save it
        validated = validate_temp(explicit_target)
        set_target_setpoint(user_id, validated)
        return validated
    
    # Try to get saved setpoint from database
    saved_setpoint = state.get("target_setpoint")
    if saved_setpoint is not None:
        return validate_temp(saved_setpoint)
    
    # Use personal_comfort from house data - this IS the target temperature in °C
    # (User sets this in the house form as their preferred temperature)
    personal_comfort = house_data.get("personal_comfort")
    if personal_comfort is not None:
        target_temp_c = validate_temp(float(personal_comfort))
    else:
        # Ultimate fallback - comfortable room temperature
        target_temp_c = 22.0
    
    # Save the value so it persists
    set_target_setpoint(user_id, target_temp_c)
    return target_temp_c


# ============================================================
# Simulation Functions
# ============================================================

def run_simulation_step(username: str) -> dict:
    """
    Run one simulation step for a user (basic version without HVAC).
    Uses GenAI to predict temperature change.
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

    # Validate weather data
    raw_weather = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing"}

    weather_rows = _extract_weather_rows(raw_weather)
    if not weather_rows:
        return {"error": "Weather rows missing"}

    # Select current weather row
    sim_time = datetime.now()
    weather = _select_weather_row(weather_rows, sim_time) or weather_rows[0]
    
    if "temperature_2m" not in weather:
        return {"error": "temperature_2m missing from weather data"}

    outdoor_temp = float(weather["temperature_2m"])

    # Get or initialize indoor temperature
    indoor_temp = state.get("simulated_temp")
    if indoor_temp is None:
        indoor_temp = outdoor_temp
        update_simulated_temp(user_id, indoor_temp)

    # Use GenAI to predict new temperature
    result = predict_temperature(
        house_data=house_data,
        current_indoor_temp_c=float(indoor_temp),
        weather_data=weather,
        hvac_mode="off",
        timestep_minutes=DEFAULT_TIMESTEP_MINUTES
    )
    
    new_temp = result.get("predicted_temp_c", indoor_temp)

    # Save new temperature
    update_simulated_temp(user_id, new_temp)

    return {
        "T_in_prev": round(float(indoor_temp), 2),
        "T_in_new": round(new_temp, 2),
        "T_out": outdoor_temp,
    }


# ============================================================
# HVAC AI Integration
# ============================================================

def run_hvac_ai(username: str, target_temp_c: float = None) -> dict:
    """
    Run the HVAC AI to generate an optimized 24-hour schedule using GenAI.
    
    Args:
        username: User's username
        target_temp_c: Desired temperature setpoint in Celsius
                      (None = use saved setpoint or derive from personal_comfort)
    
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
    
    # Determine target temperature (personal_comfort IS the target temp in °C now)
    target_temp_c = _get_target_temperature(user_id, state, house_data, target_temp_c)
    
    # Validate weather data
    raw_weather = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing - please fetch weather first"}
    
    weather_rows = _extract_weather_rows(raw_weather)
    if not weather_rows:
        return {"error": "Weather rows missing"}
    
    # Get current indoor temperature
    indoor_temp = state.get("simulated_temp")
    if indoor_temp is None:
        # Initialize from first weather row
        first_temp = weather_rows[0].get("temperature_2m", 20)
        indoor_temp = float(first_temp)
        update_simulated_temp(user_id, indoor_temp)
    
    # Generate HVAC schedule using GenAI
    schedule = generate_hvac_schedule(
        house_data=house_data,
        weather_rows=weather_rows,
        current_indoor_temp_c=float(indoor_temp),
        personal_comfort=target_temp_c,  # Pass target temp directly
        target_temp_c=target_temp_c
    )
    
    # Get current action and upcoming notifications
    current_action = get_current_hvac_action(schedule)
    upcoming = get_upcoming_actions(schedule, count=5)
    
    # Store schedule in database
    schedule_dict = schedule.to_dict()
    set_hvac_sim(user_id, schedule_dict)
    
    # Format notifications for frontend with relative time
    notifications = []
    current_hour = datetime.now().hour
    
    for action in upcoming:
        # Calculate hours until this action
        hours_away = action.hour - current_hour
        if hours_away < 0:
            hours_away += 24
        
        # Create relative time label
        if hours_away == 0:
            time_label = "Now"
        elif hours_away == 1:
            time_label = "In 1 hour"
        else:
            time_label = f"In {hours_away} hours"
        
        notifications.append({
            "mode": action.mode,
            "start_time": action.start_time,
            "end_time": action.end_time,
            "power_kw": action.power_kw,
            "cost": action.cost,
            "reason": action.reason,
            "time_label": time_label,
            "hours_away": hours_away,
            "message": f"{time_label}: {action.mode.upper()} {action.start_time}-{action.end_time} ({action.power_kw:.1f}kW, ${action.cost:.2f})"
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
    Run one simulation step with HVAC AI control using GenAI.
    
    This predicts the indoor temperature and determines HVAC action
    based on the current schedule, weather, and house properties.
    Auto-generates HVAC schedule if none exists.
    
    Args:
        username: User's username
        target_temp_c: Target temperature (None = use saved setpoint or derive from personal_comfort)
    
    Returns:
        Dict with previous temp, new temp, outdoor temp, HVAC mode, power, schedule info, etc.
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
    
    # Determine target temperature (personal_comfort IS the target temp now)
    target_temp_c = _get_target_temperature(user_id, state, house_data, target_temp_c)
    
    # Validate weather data
    raw_weather = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing"}
    
    weather_rows = _extract_weather_rows(raw_weather)
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
    
    # Get HVAC schedule (if exists) - auto-generate if missing
    hvac_schedule = state.get("hvac_sim")
    schedule_generated = False
    notifications = []
    summary = None
    
    if hvac_schedule is None:
        # Auto-generate schedule on first simulation call
        try:
            schedule = generate_hvac_schedule(
                house_data=house_data,
                weather_rows=weather_rows,
                current_indoor_temp_c=float(indoor_temp),
                personal_comfort=target_temp_c,
                target_temp_c=target_temp_c
            )
            hvac_schedule = schedule.to_dict()
            set_hvac_sim(user_id, hvac_schedule)
            schedule_generated = True
            
            # Get upcoming actions for notifications
            upcoming = get_upcoming_actions(schedule, count=5)
            for action in upcoming:
                notifications.append({
                    "mode": action.mode,
                    "start_time": action.start_time,
                    "end_time": action.end_time,
                    "power_kw": action.power_kw,
                    "cost": action.cost,
                    "reason": action.reason
                })
            
            summary = {
                "total_cost_24h": round(schedule.total_cost, 2),
                "total_energy_24h_kwh": round(schedule.total_energy_kwh, 2),
                "comfort_score": round(schedule.comfort_score, 1),
                "current_temp_c": round(float(indoor_temp), 1),
                "target_temp_c": target_temp_c
            }
        except Exception as e:
            print(f"Auto-schedule generation failed: {e}")
            hvac_schedule = None
    else:
        # Extract notifications from existing schedule (present and future only)
        if "actions" in hvac_schedule:
            current_hour = sim_time.hour
            
            def hours_until(action_hour):
                """Calculate hours until an action (0 = now, positive = future)."""
                diff = action_hour - current_hour
                if diff < 0:
                    diff += 24  # Wrap around midnight
                return diff
            
            # Sort actions by how soon they occur
            sorted_actions = sorted(
                hvac_schedule.get("actions", []),
                key=lambda a: hours_until(int(str(a.get("start_time", "00:00")).split(":")[0]))
            )
            
            for action in sorted_actions:
                try:
                    action_hour = int(str(action.get("start_time", "00:00")).split(":")[0])
                    hours_away = hours_until(action_hour)
                    
                    # Only show present (now) and future actions (up to 8 hours ahead)
                    if hours_away <= 8 and action.get("mode", "off") != "off":
                        # Add relative time info
                        if hours_away == 0:
                            time_label = "Now"
                        elif hours_away == 1:
                            time_label = "In 1 hour"
                        else:
                            time_label = f"In {hours_away} hours"
                        
                        notifications.append({
                            "mode": action.get("mode", "off"),
                            "start_time": action.get("start_time", ""),
                            "end_time": action.get("end_time", ""),
                            "power_kw": action.get("power_kw", 0),
                            "cost": action.get("cost", 0),
                            "reason": action.get("reason", "Scheduled action"),
                            "time_label": time_label,
                            "hours_away": hours_away
                        })
                        
                        if len(notifications) >= 5:
                            break
                except Exception as e:
                    print(f"Error parsing action: {e}")
                    pass
        
        summary = {
            "total_cost_24h": round(hvac_schedule.get("total_cost", 0), 2),
            "total_energy_24h_kwh": round(hvac_schedule.get("total_energy_kwh", 0), 2),
            "comfort_score": round(hvac_schedule.get("comfort_score", 0), 1),
            "current_temp_c": round(float(indoor_temp), 1),
            "target_temp_c": target_temp_c
        }
    
    # Use GenAI to simulate step with HVAC control
    result = simulate_step_with_hvac(
        house_data=house_data,
        current_indoor_temp_c=float(indoor_temp),
        target_temp_c=target_temp_c,
        weather_data=weather_row,
        hvac_schedule=hvac_schedule,
        personal_comfort=target_temp_c  # Pass target temp directly
    )
    
    # Extract results
    new_temp = result.get("new_temp_c", indoor_temp)
    hvac_mode = result.get("hvac_mode", "off")
    hvac_power = result.get("hvac_power_kw", 0)
    
    # Save new temperature
    update_simulated_temp(user_id, new_temp)
    
    # Map HVAC mode for UI
    if hvac_mode in ["heat", "pre-heat", "heating"]:
        hvac_status = "heating"
    elif hvac_mode in ["cool", "pre-cool", "cooling"]:
        hvac_status = "cooling"
    else:
        hvac_status = "off"
    
    # Extract energy and cost data
    energy_kwh = result.get("hvac_energy_kwh", 0)
    electricity_rate = result.get("electricity_rate", 0.12)
    cost = result.get("cost_this_step", energy_kwh * electricity_rate)
    
    response = {
        "T_in_prev": round(float(indoor_temp), 2),
        "T_in_new": round(new_temp, 2),
        "T_out": round(float(weather_row.get("temperature_2m", 20)), 2),
        "hvac_mode": hvac_status,
        "hvac_power_kw": round(hvac_power, 2),
        "hvac_energy_kwh": round(energy_kwh, 4),
        "electricity_rate": electricity_rate,
        "cost_this_step": round(cost, 4),
        "target_temp": target_temp_c,
        "reason": result.get("reason", ""),
        "has_schedule": hvac_schedule is not None,
        "schedule_generated": schedule_generated
    }
    
    # Include schedule data if available
    if notifications:
        response["notifications"] = notifications
    if summary:
        response["summary"] = summary
    
    return response


# ============================================================
# Setpoint Management
# ============================================================

def update_target_setpoint(username: str, target_temp_c: float) -> dict:
    """
    Update the user's target temperature setpoint.
    This is called when user manually adjusts the thermostat.
    
    Args:
        username: User's username
        target_temp_c: New target temperature in Celsius
    
    Returns:
        Dict with success status and new setpoint
    """
    user_id = get_user_id(username)
    if user_id is None:
        return {"error": "User not found"}
    
    # Validate range (reasonable indoor temperatures)
    if not 15 <= target_temp_c <= 30:
        return {"error": "Target temperature must be between 15°C and 30°C"}
    
    # Save to database
    set_target_setpoint(user_id, target_temp_c)
    
    return {
        "success": True,
        "target_temp_c": target_temp_c,
        "message": f"Target temperature set to {target_temp_c}°C"
    }


def get_current_setpoint(username: str) -> dict:
    """
    Get the user's current target temperature setpoint.
    
    Returns:
        Dict with target temperature and source (saved/derived/default)
    """
    state = get_user_state(username)
    if state is None:
        return {"error": "User not found"}
    
    user_id = state.get("id")
    saved_setpoint = state.get("target_setpoint")
    
    if saved_setpoint is not None:
        return {
            "target_temp_c": saved_setpoint,
            "source": "saved"
        }
    
    # Get from personal_comfort in house data (this IS the target temp now)
    house = state.get("house")
    if house:
        house_data = house.get("data", house) if isinstance(house, dict) else house
        personal_comfort = house_data.get("personal_comfort")
        if personal_comfort is not None:
            # personal_comfort is now directly the temperature in °C
            target_temp_c = max(15.0, min(30.0, float(personal_comfort)))
            # Save it so it persists
            set_target_setpoint(user_id, target_temp_c)
            return {
                "target_temp_c": target_temp_c,
                "source": "personal_comfort"
            }
    
    # Default fallback
    return {
        "target_temp_c": 22.0,
        "source": "default"
    }


# ============================================================
# Schedule Utilities
# ============================================================

def get_hvac_schedule_summary(username: str) -> dict:
    """
    Get a summary of the current HVAC schedule.
    
    Returns:
        Dict with schedule summary or error
    """
    state = get_user_state(username)
    if state is None:
        return {"error": "User not found"}
    
    hvac_schedule = state.get("hvac_sim")
    if hvac_schedule is None:
        return {"error": "No HVAC schedule found - run HVAC AI first"}
    
    # Count active hours by mode
    mode_counts = {"heat": 0, "cool": 0, "pre-heat": 0, "pre-cool": 0, "off": 0}
    for action in hvac_schedule.get("actions", []):
        mode = action.get("mode", "off")
        if mode in mode_counts:
            mode_counts[mode] += 1
        else:
            mode_counts["off"] += 1
    
    return {
        "generated_at": hvac_schedule.get("generated_at"),
        "total_cost": hvac_schedule.get("total_cost", 0),
        "total_energy_kwh": hvac_schedule.get("total_energy_kwh", 0),
        "comfort_score": hvac_schedule.get("comfort_score", 0),
        "hours_by_mode": mode_counts,
        "actions_count": len(hvac_schedule.get("actions", []))
    }


def apply_temporary_adjustment(username: str, adjustment_c: float, duration_minutes: int = 60) -> dict:
    """
    Apply a temporary temperature adjustment (+/- degrees) for a specified duration.
    This asks GenAI when is the best time to apply the adjustment considering
    current conditions, energy costs, and comfort.
    
    Args:
        username: User's username
        adjustment_c: Temperature adjustment in °C (positive = warmer, negative = cooler)
        duration_minutes: How long the adjustment should last (default 60 min)
    
    Returns:
        Dict with adjustment details and AI recommendation
    """
    import google.generativeai as genai
    import os
    import json
    
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
    
    # Get current temperatures
    indoor_temp = state.get("simulated_temp", 22.0)
    current_setpoint = get_current_setpoint(username)
    base_target = current_setpoint.get("target_temp_c", 22.0)
    
    # Calculate new temporary target
    temp_target = base_target + adjustment_c
    temp_target = max(15.0, min(30.0, temp_target))  # Clamp to valid range
    
    # Get weather data
    raw_weather = state.get("weather")
    weather_rows = _extract_weather_rows(raw_weather) if raw_weather else []
    current_weather = _select_weather_row(weather_rows, datetime.now()) or {}
    outdoor_temp = current_weather.get("temperature_2m", 20)
    
    # Get HVAC type
    hvac_type = house_data.get("hvac_type", "central_ac")
    
    # Configure GenAI
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY", os.getenv("GEMINI_API_KEY", "")))
    model = genai.GenerativeModel("gemini-2.0-flash")
    
    # Build prompt for AI recommendation
    prompt = f"""You are an intelligent HVAC optimization assistant.

A user wants to make a TEMPORARY temperature adjustment:
- Current indoor temperature: {indoor_temp:.1f}°C
- Current target (setpoint): {base_target:.1f}°C
- Requested adjustment: {'+' if adjustment_c > 0 else ''}{adjustment_c:.1f}°C
- New temporary target: {temp_target:.1f}°C
- Duration: {duration_minutes} minutes
- Current outdoor temperature: {outdoor_temp}°C
- HVAC system type: {hvac_type}
- Current hour: {datetime.now().hour}

Consider:
1. Is this adjustment reasonable given conditions?
2. How much energy will this cost?
3. Should HVAC start immediately or is there a better time?
4. How quickly can the target be reached?

Return JSON with this exact structure:
{{
    "apply_now": true or false,
    "reason": "brief explanation",
    "hvac_mode": "heat" or "cool" or "off",
    "estimated_time_to_target_minutes": number,
    "estimated_energy_kwh": number,
    "estimated_cost": number,
    "recommended_action": "description of what HVAC will do",
    "comfort_impact": "positive" or "neutral" or "minor discomfort"
}}"""
    
    try:
        response = model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.1
            }
        )
        ai_response = json.loads(response.text)
    except Exception as e:
        # Fallback recommendation
        need_heat = temp_target > indoor_temp
        ai_response = {
            "apply_now": True,
            "reason": "Applying adjustment immediately (AI unavailable)",
            "hvac_mode": "heat" if need_heat else "cool",
            "estimated_time_to_target_minutes": abs(temp_target - indoor_temp) * 10,
            "estimated_energy_kwh": abs(temp_target - indoor_temp) * 0.5,
            "estimated_cost": abs(temp_target - indoor_temp) * 0.5 * 0.12,
            "recommended_action": f"{'Heating' if need_heat else 'Cooling'} to {temp_target:.1f}°C",
            "comfort_impact": "positive"
        }
    
    # Apply the adjustment if AI recommends it
    if ai_response.get("apply_now", True):
        set_target_setpoint(user_id, temp_target)
    
    return {
        "success": True,
        "adjustment_applied": ai_response.get("apply_now", True),
        "base_target_c": base_target,
        "temporary_target_c": temp_target,
        "adjustment_c": adjustment_c,
        "duration_minutes": duration_minutes,
        "current_indoor_c": indoor_temp,
        "outdoor_temp_c": outdoor_temp,
        "ai_recommendation": ai_response,
        "timestamp": datetime.now().isoformat()
    }
