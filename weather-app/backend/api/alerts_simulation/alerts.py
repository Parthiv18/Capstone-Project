"""
Appliance Alerts Module
Uses Google GenAI to generate optimal appliance schedules based on HVAC data.
Helps users save energy and costs by running appliances at optimal times.
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
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
# Cache / Rate-limit Settings
# ============================================================

# How many minutes must pass before a normal (non-forced) refresh is allowed
CACHE_TTL_MINUTES = 60  # 1 hour cache — change to taste

# How many minutes must pass before a *forced* refresh is allowed
# This protects the quota even when the user hammers the "Refresh" button
FORCE_REFRESH_COOLDOWN_MINUTES = 5

# ============================================================
# Appliance Power Consumption Data (kW)
# Maps exact appliance names from house form to power consumption
# ============================================================

APPLIANCE_POWER_KW = {
    "Electric Space Heater": 1.5,
    "Portable Air Conditioner": 1.2,
    "Electric Water Heater": 4.5,
    "Gas Water Heater": 0.3,
    "Oven (Electric or Gas)": 2.5,
    "Stove / Cooktop (Electric, Gas, or Induction)": 2.0,
    "Clothes Dryer (Electric or Gas)": 3.0,
    "Washing Machine (hot water cycles)": 0.5,
    "Dishwasher (especially drying cycles)": 1.8,
    "Electric Vehicle Charger (Level 1 or Level 2)": 7.2,
}

APPLIANCE_RUN_TIMES = {
    "Electric Space Heater": 120,
    "Portable Air Conditioner": 180,
    "Electric Water Heater": 30,
    "Gas Water Heater": 30,
    "Oven (Electric or Gas)": 60,
    "Stove / Cooktop (Electric, Gas, or Induction)": 45,
    "Clothes Dryer (Electric or Gas)": 60,
    "Washing Machine (hot water cycles)": 45,
    "Dishwasher (especially drying cycles)": 90,
    "Electric Vehicle Charger (Level 1 or Level 2)": 480,
}

APPLIANCE_HEAT_GENERATORS = {
    "Electric Space Heater": True,
    "Portable Air Conditioner": False,
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
# Cache Helpers
# ============================================================

def _parse_generated_at(cached: Dict[str, Any]) -> Optional[datetime]:
    """Return the datetime the cached result was generated, or None if unparseable."""
    date_str = cached.get("generated_date", "")
    time_str = cached.get("generated_time", "")
    if not date_str or not time_str:
        return None
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _is_cache_fresh(cached: Dict[str, Any], ttl_minutes: int) -> bool:
    """Return True when the cached result is younger than ttl_minutes."""
    generated_at = _parse_generated_at(cached)
    if generated_at is None:
        return False
    age = datetime.now() - generated_at
    return age < timedelta(minutes=ttl_minutes)


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
    """Build a comprehensive prompt for GenAI to generate appliance schedules starting from current time."""

    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute

    hvac_actions = hvac_schedule.get("actions", [])
    hvac_summary = []
    total_hvac_energy = hvac_schedule.get("total_energy_kwh", 0)

    for action in hvac_actions:
        action_hour = action.get("hour", 0)
        hours_diff = action_hour - current_hour
        if hours_diff < 0:
            hours_diff += 24
        if hours_diff <= 12:
            mode = action.get("mode", "off")
            power = action.get("power_kw", 0)
            if hours_diff == 0:
                time_label = "NOW"
            elif hours_diff == 1:
                time_label = "in 1 hour"
            else:
                time_label = f"in {hours_diff} hours"
            hvac_summary.append(
                f"{action_hour:02d}:00 ({time_label}): Mode: {mode}, Power: {power:.2f} kW"
            )

    weather_summary = []
    for i, row in enumerate(weather_data[:12]):
        temp = row.get("temperature_2m", 20)
        humidity = row.get("relative_humidity_2m", 50)
        if i == 0:
            time_label = "NOW"
        elif i == 1:
            time_label = "in 1 hour"
        else:
            time_label = f"in {i} hours"
        forecast_hour = (current_hour + i) % 24
        weather_summary.append(
            f"{forecast_hour:02d}:00 ({time_label}): {temp}°C, {humidity}% humidity"
        )

    appliance_info = []
    for app in appliances:
        power = APPLIANCE_POWER_KW.get(app, 1.0)
        run_time = APPLIANCE_RUN_TIMES.get(app, 30)
        is_heat_generator = APPLIANCE_HEAT_GENERATORS.get(app, False)
        heat_note = " (generates heat)" if is_heat_generator else ""
        appliance_info.append(
            f"- {app}: {power} kW, typical run time: {run_time} minutes{heat_note}"
        )

    prompt = f"""You are an energy optimization AI assistant. Generate optimal appliance schedules starting from NOW.

## CURRENT TIME: {current_hour:02d}:{current_minute:02d}

## House Information
- Size: {house_data.get('home_size', 1500)} sqft
- Insulation: {house_data.get('insulation_quality', 'average')}
- HVAC Type: {house_data.get('hvac_type', 'central_ac')}
- Current Indoor Temperature: {current_temp_c:.1f}°C
- Target Temperature: {target_temp_c:.1f}°C

## HVAC Schedule (Present & Future)
Total HVAC Energy: {total_hvac_energy:.2f} kWh
{chr(10).join(hvac_summary)}

## Weather Forecast (Present & Future)
{chr(10).join(weather_summary)}

## User's Appliances
{chr(10).join(appliance_info)}

## Task
For each appliance, determine:
1. The BEST time to run it (MUST be present or future - starting from {current_hour:02d}:00 or later)
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
CRITICAL: All times MUST be {current_hour:02d}:00 or later (present/future only, NO past times).

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

    Caching strategy
    ----------------
    • Normal request  → serve cache if younger than CACHE_TTL_MINUTES (default 60 min).
    • Force-refresh   → still honour a hard cooldown of FORCE_REFRESH_COOLDOWN_MINUTES
                        (default 5 min) so a user can't spam the button and burn quota.
    """
    if not GENAI_KEY:
        return {"error": "GenAI API key not configured"}

    state = get_user_state(username)
    if state is None:
        return {"error": "User not found"}

    user_id = state.get("id")
    if user_id is None:
        return {"error": "User ID missing"}

    # ---- Cache logic ----
    cached_alerts = state.get("appliance_alerts")

    if cached_alerts:
        if force_refresh:
            # Even on forced refresh, enforce the hard cooldown to protect quota
            if _is_cache_fresh(cached_alerts, FORCE_REFRESH_COOLDOWN_MINUTES):
                generated_at = _parse_generated_at(cached_alerts)
                seconds_left = (
                    FORCE_REFRESH_COOLDOWN_MINUTES * 60
                    - (datetime.now() - generated_at).total_seconds()
                )
                cached_alerts["_cache_note"] = (
                    f"Refresh cooldown active — please wait "
                    f"{int(seconds_left // 60)}m {int(seconds_left % 60)}s before refreshing again."
                )
                return cached_alerts
        else:
            # Normal request: serve cache if still within TTL
            if _is_cache_fresh(cached_alerts, CACHE_TTL_MINUTES):
                return cached_alerts

    # ---- Gather required data ----
    house = state.get("house")
    if not house:
        return {"error": "House data missing - please submit house information first"}

    house_data = house.get("data", house) if isinstance(house, dict) else house
    appliances = house.get("appliances", [])

    if not appliances:
        return {
            "error": "No appliances found",
            "message": "Please add appliances in your house settings to get personalized alerts",
        }

    hvac_sim = state.get("hvac_sim")
    if not hvac_sim:
        return {"error": "HVAC schedule missing - please run HVAC simulation first"}

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
                weather_rows = (
                    weather_data.get("rows", []) if isinstance(weather_data, dict) else []
                )
            else:
                weather_rows = raw_weather
        else:
            weather_rows = []
    else:
        weather_rows = []

    if not weather_rows:
        return {"error": "Weather data rows missing"}

    current_temp = state.get("simulated_temp", 22.0)
    target_temp = state.get("target_setpoint", 22.0)

    prompt = build_genai_prompt(
        appliances=appliances,
        hvac_schedule=hvac_sim,
        weather_data=weather_rows,
        house_data=house_data,
        current_temp_c=float(current_temp),
        target_temp_c=float(target_temp),
    )

    # ---- Call GenAI ----
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.2,
                max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )

        response_text = response.text.strip()

        # Strip markdown fences if present
        if "```" in response_text:
            import re
            response_text = re.sub(r"^```(?:json)?\s*\n?", "", response_text)
            response_text = re.sub(r"\n?```\s*$", "", response_text)
            response_text = response_text.strip()

        # Parse JSON — three fallback strategies
        result = None
        parse_error = None

        try:
            result = json.loads(response_text)
        except json.JSONDecodeError as e:
            parse_error = str(e)

        if result is None:
            import re
            first_brace = response_text.find("{")
            last_brace = response_text.rfind("}")
            if first_brace != -1 and last_brace > first_brace:
                try:
                    result = json.loads(response_text[first_brace : last_brace + 1])
                except json.JSONDecodeError:
                    pass

        if result is None:
            import re
            fixed_text = re.sub(r",\s*([}\]])", r"\1", response_text)
            try:
                result = json.loads(fixed_text)
            except json.JSONDecodeError:
                pass

        if result is None:
            print(f"GenAI Response parsing failed. Raw response:\n{response_text[:1000]}")
            return {
                "error": "Failed to parse GenAI response",
                "message": "The AI returned an invalid response format. Please try again.",
                "debug_info": parse_error,
            }

        # Ensure required keys exist
        result.setdefault("appliance_schedules", [])
        result.setdefault("alerts", [])

        # Post-process: add human-readable time labels
        now = datetime.now()
        current_hour = now.hour

        for schedule in result.get("appliance_schedules", []):
            try:
                start_time = schedule.get("optimal_start_time", "00:00")
                start_hour = int(start_time.split(":")[0])
                hours_diff = start_hour - current_hour
                if hours_diff < 0:
                    hours_diff += 24
                if hours_diff == 0:
                    time_label = "NOW"
                elif hours_diff == 1:
                    time_label = "in 1 hour"
                else:
                    time_label = f"in {hours_diff} hours"
                schedule["time_label"] = time_label
                schedule["hours_away"] = hours_diff
            except (ValueError, IndexError):
                schedule["time_label"] = schedule.get("optimal_start_time", "")
                schedule["hours_away"] = 0

        # Metadata
        result["generated_date"] = datetime.now().strftime("%Y-%m-%d")
        result["generated_time"] = datetime.now().strftime("%H:%M:%S")
        result["username"] = username
        result["appliances_analyzed"] = appliances

        # Persist to DB
        set_appliance_alerts(user_id, result)

        return result

    except Exception as e:
        return {
            "error": f"GenAI API error: {str(e)}",
            "message": "Failed to generate appliance alerts",
        }


# ============================================================
# API Endpoints
# ============================================================

@router.get("/alerts/{username}")
def get_alerts(username: str, refresh: bool = False):
    """
    Get appliance alerts and schedules for a user.

    Query params:
        refresh: If true, request fresh alerts (still subject to cooldown).
    """
    try:
        result = generate_appliance_alerts(username, force_refresh=refresh)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"Alerts Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts/{username}/refresh")
def refresh_alerts(username: str):
    """Force regenerate appliance alerts for a user (subject to cooldown)."""
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
                detail="No cached alerts found - call /alerts/{username} to generate",
            )
        return alerts
    except HTTPException:
        raise
    except Exception as e:
        print(f"Cached Alerts Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))