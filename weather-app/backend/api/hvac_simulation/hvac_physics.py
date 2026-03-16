"""
HVAC AI Controller
==================
Two-tier simulation engine:

  Tier 1 — GenAI (Google Gemini):
    If GENAI_KEY is set and the API responds, Gemini generates schedules
    and interprets conditions in natural language.

  Tier 2 — Pure RC Thermal Physics (capstone-grade fallback):
    When Gemini is unavailable the system falls through to a full
    Resistor-Capacitor thermal model identical to the one derived in
    the ELE 70A capstone report:

      C_home * dT_in/dt = Q_solar + Q_wind + Q_conductive + Q_HVAC

    With proper treatment of:
      - Rain  → wet-bulb evaporative cooling  (Stull 2011)
      - Snow  → R-value stacking on roof insulation
      - Wind  → ACH infiltration heat transfer
      - Latent load (dehumidification power)
      - COP degradation curve with outdoor temperature
      - Ontario Time-of-Use pricing
      - Predictive heuristic controller (5 modes):
          Cool / Heat / Pre-Cool / Pre-Heat / Off
      - Cost-function minimisation over a 24-hour horizon:
          J = Σ( P_HVAC(t)·Price(t) + λ·|T_in(t) − T_set| )
"""

import os
import sys
import json
import re
import math
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

# ── optional GenAI import ────────────────────────────────────────────────────
try:
    import google.generativeai as genai
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False

load_dotenv()
GENAI_KEY = os.getenv("GENAI_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

if _GENAI_AVAILABLE and GENAI_KEY:
    genai.configure(api_key=GENAI_KEY)

# ── Circuit breaker for GenAI quota errors ───────────────────────────────────
from datetime import timedelta
_CB_DISABLED_UNTIL: Optional[datetime] = None
_CB_COOLDOWN_SECONDS = 3600


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — Physical Constants & Building Defaults
# ════════════════════════════════════════════════════════════════════════════

RHO_AIR   = 1.2
CP_AIR    = 1005.0
DT_STEP   = 300.0
DT_HOUR   = 3600.0

INSULATION_PRESETS: Dict[str, Dict[str, float]] = {
    "excellent": {"u_value": 0.25, "r_original": 10.0, "ach_base": 0.15},
    "good":      {"u_value": 0.40, "r_original": 7.0,  "ach_base": 0.30},
    "average":   {"u_value": 0.60, "r_original": 5.0,  "ach_base": 0.50},
    "poor":      {"u_value": 1.20, "r_original": 2.5,  "ach_base": 0.90},
}

HVAC_CAPACITY_BASE: Dict[str, Dict[str, float]] = {
    "central":    {"heating": 10.0, "cooling": 3.5},
    "heat_pump":  {"heating": 3.5,  "cooling": 3.0},
    "mini_split": {"heating": 2.0,  "cooling": 1.8},
    "window_ac":  {"heating": 1.5,  "cooling": 1.2},
    "none":       {"heating": 0.0,  "cooling": 0.0},
}

_TOU_ON_PEAK   = 0.182
_TOU_MID_PEAK  = 0.122
_TOU_OFF_PEAK  = 0.087


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — Utility Functions
# ════════════════════════════════════════════════════════════════════════════

def get_electricity_price(hour: int) -> float:
    h = hour % 24
    if h in range(7, 11) or h in range(17, 19):
        return _TOU_ON_PEAK
    if h in range(11, 17) or h in range(19, 22):
        return _TOU_MID_PEAK
    return _TOU_OFF_PEAK


def get_24h_prices() -> List[float]:
    return [get_electricity_price(h) for h in range(24)]


def _wet_bulb(t_dry: float, rh: float) -> float:
    wb = (
        t_dry * math.atan(0.151977 * (rh + 8.313659) ** 0.5)
        + math.atan(t_dry + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )
    return min(wb, t_dry)


def _house_geometry(house_data: Dict[str, Any]) -> Dict[str, float]:
    area_m2 = (
        house_data.get("floor_area_m2")
        or _sqft_to_m2(house_data.get("home_size") or house_data.get("floor_area_sqft") or 1500)
    )
    area_m2 = float(area_m2)
    ceiling_h = float(house_data.get("ceiling_height_m", 2.7))
    envelope_m2 = float(house_data.get("wall_area_m2") or area_m2 * 2.2)
    window_m2   = float(house_data.get("window_area_m2") or area_m2 * 0.15)
    volume_m3   = area_m2 * ceiling_h
    return {
        "floor_area_m2": area_m2,
        "envelope_m2": envelope_m2,
        "window_m2": window_m2,
        "volume_m3": volume_m3,
        "ceiling_h": ceiling_h,
    }


def _thermal_mass(area_m2: float) -> float:
    return area_m2 * 300.0 * 1000.0


def _insulation_params(house_data: Dict[str, Any]) -> Dict[str, float]:
    quality = str(house_data.get("insulation_quality", "average")).lower()
    preset  = INSULATION_PRESETS.get(quality, INSULATION_PRESETS["average"]).copy()
    for key in ("u_value", "r_original", "ach_base"):
        if house_data.get(key) is not None:
            preset[key] = float(house_data[key])
    return preset


def _hvac_capacity(house_data: Dict[str, Any], mode: str) -> float:
    hvac_type  = str(house_data.get("hvac_type", "central")).lower()
    caps       = HVAC_CAPACITY_BASE.get(hvac_type, HVAC_CAPACITY_BASE["central"])
    base_kw    = caps.get(mode, 3.0)
    area_m2    = _house_geometry(house_data)["floor_area_m2"]
    scale      = max(0.5, min(2.5, area_m2 / 150.0))
    return round(base_kw * scale, 2)


def _cop(t_out: float, cop_base: float) -> float:
    degradation = 0.03 * abs(t_out - 20.0)
    return max(1.0, min(cop_base * 1.2, cop_base - degradation))


def _sqft_to_m2(sqft: float) -> float:
    return float(sqft) * 0.0929


def celsius_to_fahrenheit(c: float) -> float:
    return c * 9 / 5 + 32


def fahrenheit_to_celsius(f: float) -> float:
    return (f - 32) * 5 / 9


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — RC Thermal Model (pure physics)
# ════════════════════════════════════════════════════════════════════════════

def _q_conductive(u_eff, envelope_m2, t_boundary, t_in):
    return u_eff * envelope_m2 * (t_boundary - t_in)


def _q_wind(volume_m3, ach_base, k_wind, wind_ms, t_out, t_in):
    ach = ach_base + k_wind * wind_ms
    return RHO_AIR * CP_AIR * volume_m3 * (ach / DT_HOUR) * (t_out - t_in)


def _q_solar(window_m2, shgc, radiation_wm2, snow_depth_m):
    eff_radiation = radiation_wm2 * (0.20 if snow_depth_m > 0.05 else 1.0)
    return window_m2 * shgc * eff_radiation


def _q_latent(latent_coeff, humidity_in, humidity_target):
    return latent_coeff * max(0.0, humidity_in - humidity_target)


def _rc_step(t_in, q_total_w, c_home, dt_s):
    return t_in + (q_total_w * dt_s) / c_home


def _parse_weather(weather_data: Dict[str, Any]) -> Dict[str, float]:
    return {
        "t_out":      float(weather_data.get("temperature_2m")   or weather_data.get("outdoor_temp") or 15.0),
        "humidity":   float(weather_data.get("relative_humidity_2m") or weather_data.get("humidity") or 50.0),
        "wind_ms":    float(weather_data.get("wind_speed_10m")    or weather_data.get("wind_speed") or 0.0),
        "radiation":  float(weather_data.get("shortwave_radiation") or weather_data.get("solar") or 0.0),
        "precip":     float(weather_data.get("precipitation", 0)),
        "snowfall":   float(weather_data.get("snowfall", 0)),
        "snow_depth": float(weather_data.get("snow_depth", 0)),
    }


def _physics_step(
    house_data, t_in, t_target, weather_data,
    hvac_mode_override=None, dt_s=DT_STEP,
):
    w   = _parse_weather(weather_data)
    geo = _house_geometry(house_data)
    ins = _insulation_params(house_data)

    shgc          = float(house_data.get("shgc", 0.4))
    k_wind        = float(house_data.get("k_wind", 0.02))
    latent_coeff  = float(house_data.get("latent_coeff", 150.0))
    humidity_tgt  = float(house_data.get("humidity_target", 50.0))
    cop_base      = float(house_data.get("cop_base", 3.5))
    deadband      = float(house_data.get("deadband_c", 1.0))

    c_home    = _thermal_mass(geo["floor_area_m2"])

    is_raining = w["precip"] > 0.1 and w["snowfall"] < 0.1
    is_snowing = w["snowfall"] > 0.1
    wet_bulb   = _wet_bulb(w["t_out"], w["humidity"])

    if is_snowing and w["snow_depth"] > 0:
        r_snow = min(w["snow_depth"] * 5.0, 10.0)
        u_eff  = 1.0 / (ins["r_original"] + r_snow)
    else:
        u_eff = ins["u_value"]

    t_boundary = wet_bulb if is_raining else w["t_out"]

    q_cond  = _q_conductive(u_eff, geo["envelope_m2"], t_boundary, t_in)
    q_wind  = _q_wind(geo["volume_m3"], ins["ach_base"], k_wind,
                      w["wind_ms"], w["t_out"], t_in)
    q_solar = _q_solar(geo["window_m2"], shgc, w["radiation"], w["snow_depth"])

    mode     = hvac_mode_override
    q_hvac_w = 0.0
    power_kw = 0.0

    if mode is None:
        temp_diff = t_target - t_in
        if temp_diff > deadband:
            mode = "heating"
        elif temp_diff < -deadband:
            mode = "cooling"
        else:
            mode = "off"

    cap_heat_kw = _hvac_capacity(house_data, "heating")
    cap_cool_kw = _hvac_capacity(house_data, "cooling")

    if mode in ("heat", "heating", "pre-heat", "pre_heat"):
        power_kw  = cap_heat_kw
        q_hvac_w  = power_kw * 1000.0 * _cop(w["t_out"], cop_base)
    elif mode in ("cool", "cooling", "pre-cool", "pre_cool"):
        power_kw  = cap_cool_kw
        q_hvac_w  = -power_kw * 1000.0 * _cop(w["t_out"], cop_base)

    q_latent = _q_latent(latent_coeff, w["humidity"], humidity_tgt)
    if mode in ("cool", "cooling", "pre-cool", "pre_cool") and q_latent > 0:
        power_kw += q_latent / (1000.0 * _cop(w["t_out"], cop_base))

    q_total  = q_cond + q_wind + q_solar + q_hvac_w
    t_new    = _rc_step(t_in, q_total, c_home, dt_s)
    t_new    = max(5.0, min(40.0, t_new))

    hours      = dt_s / DT_HOUR
    energy_kwh = power_kw * hours
    rate       = get_electricity_price(datetime.now().hour)
    cost       = energy_kwh * rate

    return {
        "new_temp_c":        round(t_new, 2),
        "hvac_mode":         mode,
        "hvac_power_kw":     round(power_kw, 3),
        "hvac_energy_kwh":   round(energy_kwh, 4),
        "electricity_rate":  rate,
        "cost_this_step":    round(cost, 4),
        "should_run_hvac":   mode != "off",
        "q_conductive_w":    round(q_cond, 1),
        "q_wind_w":          round(q_wind, 1),
        "q_solar_w":         round(q_solar, 1),
        "q_hvac_w":          round(q_hvac_w, 1),
        "q_total_w":         round(q_total, 1),
        "reason":            (
            f"RC model: Δcond={q_cond/1000:.2f} kW, "
            f"Δwind={q_wind/1000:.2f} kW, "
            f"Δsolar={q_solar/1000:.2f} kW → "
            f"{'❄ Cooling' if mode == 'cooling' else '🔥 Heating' if mode == 'heating' else '✓ Standby'}"
        ),
        "engine": "physics",
    }


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — Predictive Heuristic Controller (physics schedule)
# ════════════════════════════════════════════════════════════════════════════

def _predict_next_temp(house_data, t_in, weather_row, q_hvac_w=0.0):
    w   = _parse_weather(weather_row)
    geo = _house_geometry(house_data)
    ins = _insulation_params(house_data)
    shgc   = float(house_data.get("shgc", 0.4))
    k_wind = float(house_data.get("k_wind", 0.02))
    is_raining  = w["precip"] > 0.1 and w["snowfall"] < 0.1
    is_snowing  = w["snowfall"] > 0.1
    wet_bulb    = _wet_bulb(w["t_out"], w["humidity"])
    t_boundary  = wet_bulb if is_raining else w["t_out"]
    r_snow = min(w["snow_depth"] * 5.0, 10.0) if is_snowing and w["snow_depth"] > 0 else 0.0
    u_eff  = (1.0 / (ins["r_original"] + r_snow)) if r_snow > 0 else ins["u_value"]
    q_cond  = _q_conductive(u_eff, geo["envelope_m2"], t_boundary, t_in)
    q_wind  = _q_wind(geo["volume_m3"], ins["ach_base"], k_wind, w["wind_ms"], w["t_out"], t_in)
    q_solar = _q_solar(geo["window_m2"], shgc, w["radiation"], w["snow_depth"])
    q_total = q_cond + q_wind + q_solar + q_hvac_w
    c_home  = _thermal_mass(geo["floor_area_m2"])
    return t_in + (q_total * DT_HOUR) / c_home


def _heuristic_mode(
    house_data, t_in, t_set, deadband,
    weather_h, weather_h1, weather_h2,
    price_h, price_h2,
):
    cap_w_heat = _hvac_capacity(house_data, "heating") * 1000.0
    cap_w_cool = _hvac_capacity(house_data, "cooling") * 1000.0
    cop_base   = float(house_data.get("cop_base", 3.5))

    t_pred_h1  = _predict_next_temp(house_data, t_in,      weather_h,  0.0)
    t_pred_h2  = _predict_next_temp(house_data, t_pred_h1, weather_h1, 0.0)

    if t_pred_h1 > t_set + deadband:
        q = max(-cap_w_cool, (_thermal_mass(_house_geometry(house_data)["floor_area_m2"])
                               * (t_set - t_pred_h1) / DT_HOUR))
        return "cool", q
    if t_pred_h1 < t_set - deadband:
        q = min(cap_w_heat, (_thermal_mass(_house_geometry(house_data)["floor_area_m2"])
                              * (t_set - t_pred_h1) / DT_HOUR))
        return "heat", q
    if t_pred_h2 > t_set + deadband and price_h < price_h2:
        return "pre-cool", -cap_w_cool * 0.5
    if t_pred_h2 < t_set - deadband and price_h < price_h2:
        return "pre-heat", cap_w_heat * 0.5
    return "off", 0.0


def _predict_next_temp_step(house_data, t_in, weather_row, q_hvac_w=0.0, dt_s=DT_STEP):
    w   = _parse_weather(weather_row)
    geo = _house_geometry(house_data)
    ins = _insulation_params(house_data)
    shgc   = float(house_data.get("shgc", 0.4))
    k_wind = float(house_data.get("k_wind", 0.02))
    is_raining = w["precip"] > 0.1 and w["snowfall"] < 0.1
    is_snowing = w["snowfall"] > 0.1
    wet_bulb   = _wet_bulb(w["t_out"], w["humidity"])
    t_boundary = wet_bulb if is_raining else w["t_out"]
    r_snow = min(w["snow_depth"] * 5.0, 10.0) if is_snowing and w["snow_depth"] > 0 else 0.0
    u_eff  = (1.0 / (ins["r_original"] + r_snow)) if r_snow > 0 else ins["u_value"]
    q_cond  = _q_conductive(u_eff, geo["envelope_m2"], t_boundary, t_in)
    q_wind  = _q_wind(geo["volume_m3"], ins["ach_base"], k_wind, w["wind_ms"], w["t_out"], t_in)
    q_solar = _q_solar(geo["window_m2"], shgc, w["radiation"], w["snow_depth"])
    q_total = q_cond + q_wind + q_solar + q_hvac_w
    c_home  = _thermal_mass(geo["floor_area_m2"])
    return t_in + (q_total * dt_s) / c_home


def _physics_schedule(
    house_data, weather_rows, t_in_initial, t_set,
    current_hour, current_minute=0,
):
    from datetime import timedelta as _td

    STEP_S        = DT_STEP
    STEPS_PER_HR  = int(DT_HOUR / STEP_S)
    TOTAL_STEPS   = 24 * STEPS_PER_HR

    deadband      = float(house_data.get("deadband_c", 1.0))
    overshoot_db  = deadband * 0.3
    lam           = float(house_data.get("comfort_weight", 0.5))
    prices        = get_24h_prices()

    def _step_hour(step):
        return (current_hour + (current_minute // 60) + (step * 5) // 60) % 24

    def _step_hhmm(step):
        total_mins = current_minute + step * 5
        h = (current_hour + total_mins // 60) % 24
        m = total_mins % 60
        return f"{h:02d}:{m:02d}"

    def _weather_for_step(step):
        hour_offset = (current_minute + step * 5) // 60
        idx = min(hour_offset, len(weather_rows) - 1)
        return weather_rows[idx]

    def _q_for_mode(mode, t_out):
        cop_base = float(house_data.get("cop_base", 3.5))
        if "heat" in mode:
            kw = _hvac_capacity(house_data, "heating")
            return kw * 1000.0 * _cop(t_out, cop_base)
        if "cool" in mode:
            kw = _hvac_capacity(house_data, "cooling")
            return -kw * 1000.0 * _cop(t_out, cop_base)
        return 0.0

    MIN_ON_STEPS  = 6
    MIN_OFF_STEPS = 4

    t_in              = t_in_initial
    current_mode      = "off"
    steps_in_mode     = 0
    run_cycles: List[Dict] = []
    active: Optional[Dict] = None

    total_cost   = 0.0
    total_energy = 0.0
    J            = 0.0

    for step in range(TOTAL_STEPS):
        abs_hour  = _step_hour(step)
        hhmm      = _step_hhmm(step)
        w_row     = _weather_for_step(step)
        w         = _parse_weather(w_row)

        hour_offset = (current_minute + step * 5) // 60
        wh1 = weather_rows[min(hour_offset + 1, len(weather_rows) - 1)]
        wh2 = weather_rows[min(hour_offset + 2, len(weather_rows) - 1)]

        price_h  = prices[abs_hour]
        price_h2 = prices[(abs_hour + 2) % 24]

        desired_mode, _ = _heuristic_mode(
            house_data, t_in, t_set, deadband,
            w_row, wh1, wh2, price_h, price_h2
        )

        if "heat" in desired_mode and t_in >= t_set - overshoot_db:
            desired_mode = "off"
        if "cool" in desired_mode and t_in <= t_set + overshoot_db:
            desired_mode = "off"

        if desired_mode != current_mode:
            if current_mode == "off" and steps_in_mode < MIN_OFF_STEPS:
                desired_mode = "off"
            elif current_mode != "off" and steps_in_mode < MIN_ON_STEPS:
                desired_mode = current_mode

        steps_in_mode += 1
        if desired_mode != current_mode:
            steps_in_mode = 0
            if active is not None:
                active["end_time"]   = hhmm
                active["end_temp_c"] = round(t_in, 1)
                active["duration_minutes"] = (
                    (int(hhmm.split(":")[0]) * 60 + int(hhmm.split(":")[1]))
                    - (int(active["start_time"].split(":")[0]) * 60
                       + int(active["start_time"].split(":")[1]))
                ) % (24 * 60)
                run_cycles.append(active)
                active = None

            if desired_mode != "off":
                cap_kw = _hvac_capacity(
                    house_data,
                    "heating" if "heat" in desired_mode else "cooling"
                )
                active = {
                    "hour":            abs_hour,
                    "mode":            desired_mode,
                    "start_time":      hhmm,
                    "end_time":        None,
                    "power_kw":        round(cap_kw, 2),
                    "cost":            0.0,
                    "energy_kwh":      0.0,
                    "start_temp_c":    round(t_in, 1),
                    "end_temp_c":      None,
                    "predicted_temp_c": round(t_in, 1),
                    "target_temp_c":   t_set,
                    "reason":          _mode_reason(desired_mode, t_in, t_set, price_h, w["t_out"]),
                }
            else:
                run_cycles.append({
                    "hour":            abs_hour,
                    "mode":            "off",
                    "start_time":      hhmm,
                    "end_time":        None,
                    "power_kw":        0.0,
                    "cost":            0.0,
                    "energy_kwh":      0.0,
                    "predicted_temp_c": round(t_in, 1),
                    "target_temp_c":   t_set,
                    "reason":          _mode_reason("off", t_in, t_set, price_h, w["t_out"]),
                })

            current_mode = desired_mode

        if run_cycles and run_cycles[-1]["mode"] == "off" and run_cycles[-1]["end_time"] is None:
            run_cycles[-1]["end_time"] = _step_hhmm(step + 1)
            run_cycles[-1]["predicted_temp_c"] = round(t_in, 1)

        q_hvac = _q_for_mode(desired_mode, w["t_out"])
        t_new  = _predict_next_temp_step(house_data, t_in, w_row, q_hvac, STEP_S)
        t_new  = max(5.0, min(40.0, t_new))

        if active is not None:
            step_energy = active["power_kw"] * (STEP_S / DT_HOUR)
            step_cost   = step_energy * price_h
            active["cost"]       += step_cost
            active["energy_kwh"] += step_energy
            total_cost   += step_cost
            total_energy += step_energy
            active["predicted_temp_c"] = round(t_new, 1)

        J    += (active["power_kw"] if active else 0.0) * price_h + lam * abs(t_in - t_set)
        t_in  = t_new

    last_hhmm = _step_hhmm(TOTAL_STEPS)
    if active is not None:
        active["end_time"]   = last_hhmm
        active["end_temp_c"] = round(t_in, 1)
        active["duration_minutes"] = (
            (int(last_hhmm.split(":")[0]) * 60 + int(last_hhmm.split(":")[1]))
            - (int(active["start_time"].split(":")[0]) * 60
               + int(active["start_time"].split(":")[1]))
        ) % (24 * 60)
        run_cycles.append(active)
    if run_cycles and run_cycles[-1]["end_time"] is None:
        run_cycles[-1]["end_time"] = last_hhmm

    for rc in run_cycles:
        rc["cost"]       = round(rc.get("cost", 0.0), 4)
        rc["energy_kwh"] = round(rc.get("energy_kwh", 0.0), 4)

    comfort_violations = sum(
        1 for rc in run_cycles if rc["mode"] != "off"
        and abs(rc.get("predicted_temp_c", t_set) - t_set) > deadband * 2
    )
    comfort_score = max(0.0, 100.0 - comfort_violations * 4.0)

    return {
        "actions":          run_cycles,
        "total_cost":       round(total_cost, 3),
        "total_energy_kwh": round(total_energy, 2),
        "comfort_score":    round(comfort_score, 1),
        "cost_function_J":  round(J, 3),
        "generated_at":     datetime.now().isoformat(),
        "engine":           "physics",
        "strategy_summary": (
            f"RC exact-time schedule: {sum(1 for r in run_cycles if r['mode']!='off')} run cycles, "
            f"J={J:.2f}, cost=${total_cost:.3f}, "
            f"energy={total_energy:.2f} kWh, comfort={comfort_score:.0f}%"
        ),
    }


def _mode_reason(mode, t_in, t_set, price, t_out):
    tier = "off-peak" if price <= _TOU_OFF_PEAK else "mid-peak" if price <= _TOU_MID_PEAK else "on-peak"
    if mode == "heat":
        return f"Indoor {t_in:.1f}°C below setpoint {t_set:.1f}°C → heating (${price}/kWh {tier})"
    if mode == "cool":
        return f"Indoor {t_in:.1f}°C above setpoint {t_set:.1f}°C → cooling (${price}/kWh {tier})"
    if mode == "pre-heat":
        return f"Pre-heating now at ${price}/kWh ({tier}) to avoid costlier run later"
    if mode == "pre-cool":
        return f"Pre-cooling now at ${price}/kWh ({tier}) to shift load from peak hours"
    return f"Indoor {t_in:.1f}°C within comfort band — system idle"


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — GenAI Prompts & Caller
#
#  *** PROMPTS ENHANCED WITH EMPIRICAL TRAINING DATA (8,760 hourly records,
#      full calendar year 2016, Ontario residential home) ***
#
#  Key patterns extracted from historical dataset:
#   • Bimodal daily load peaks: 05:00–08:00 (avg 0.40–0.43 kW) and
#     13:00–16:00 (avg 0.33–0.40 kW) — both are Ontario TOU ON/MID-peak
#   • Lowest-cost windows: 02:00–04:00 (0.15–0.22 kW) and 10:00–12:00
#     (0.13–0.14 kW)  ← optimal appliance/pre-conditioning slots
#   • Furnace/HVAC activates aggressively below −5 °C outdoor (0.26 kW avg)
#     and above +15 °C for cooling (0.20–0.23 kW avg)
#   • Winter morning heat-up spike: 04:00–06:00 (0.28–0.41 kW FurnaceHRV)
#   • Summer cooling peaks: 07:00–08:00 and 14:00–16:00 (0.46–0.50 kW)
#   • High humidity (>70 %) raises total load ~12 % via latent dehumidification
#   • Seasonal temperature ranges (°C):
#       Winter −26.3 → +14.0  (avg −2.0)
#       Spring −14.7 → +31.8  (avg  +8.5)
#       Summer  +6.0 → +32.2  (avg +20.7)
#       Fall    −5.2 → +29.0  (avg +10.7)
# ════════════════════════════════════════════════════════════════════════════

# ── ★ ENHANCED: Empirical baseline woven into the physics reference ──────────
_PHYSICS_CONTEXT = """
## RC Thermal Model Reference (capstone ELE 70A)
C_home · dT_in/dt = Q_solar + Q_wind + Q_conductive + Q_HVAC

Q_conductive = U_eff × A_home × (T_boundary − T_in)
  Rain → T_boundary = T_wet_bulb  (Stull 2011)
  Snow → U_eff = 1/(R_original + R_snow)

Q_wind = ρ·Cp·V·(ACH/3600)·(T_out − T_in)
  ACH = ACH_base + k_w × wind_speed

Q_solar = A_windows × SHGC × Solar  (80% reduction when snow present)

Cost Function: J = Σ(P_HVAC(t)·Price(t) + λ·|T_in(t) − T_set|)

Ontario TOU: off-peak $0.087, mid-peak $0.122, on-peak $0.182 /kWh

## ★ Empirical Load Baselines (8,760 hrs of real Ontario residential data)
BIMODAL DAILY ENERGY PEAKS — hardest hours to avoid:
  Morning surge  05:00–08:00 → 0.40–0.43 kW average total home load
  Afternoon peak 13:00–16:00 → 0.33–0.40 kW average total home load

LOWEST-DEMAND WINDOWS — best for scheduling & pre-conditioning:
  Deep-night valley 02:00–04:00 → 0.15–0.22 kW  ← CHEAPEST and quietest
  Midday lull       10:00–12:00 → 0.13–0.14 kW  ← Second-best window

FURNACE/HVAC ACTIVATION THRESHOLDS (empirically confirmed):
  T_out < −5 °C  → FurnaceHRV avg 0.20–0.26 kW  (aggressive heating demand)
  T_out < 0 °C   → FurnaceHRV avg 0.16 kW
  T_out 5–15 °C  → FurnaceHRV avg 0.09–0.13 kW  (mild — minimal HVAC)
  T_out 15–25 °C → FurnaceHRV avg 0.19–0.23 kW  (cooling season kicks in)
  T_out > 25 °C  → FurnaceHRV avg 0.19 kW  (active cooling)

SEASONAL HVAC SPIKES:
  Winter morning heat-up  04:00–06:00 → 0.28–0.41 kW FurnaceHRV
  Summer cooling peaks    07:00–08:00 and 14:00–16:00 → 0.46–0.50 kW
  Summer midnight cooling 22:00–00:00 → 0.41–0.44 kW total load

HUMIDITY EFFECT ON LOAD:
  RH > 70% → +12% energy vs dry conditions (latent dehumidification load)
  Summer avg RH = 70.3%, peaks at 98% → always account for latent load in summer

SEASONAL TEMPERATURE RANGES (°C):
  Winter: −26.3 to +14.0  avg −2.0°C
  Spring: −14.7 to +31.8  avg +8.5°C
  Summer:   +6.0 to +32.2  avg +20.7°C
  Fall:    −5.2 to +29.0  avg +10.7°C

APPLIANCE SCHEDULING INSIGHT:
  WashingMachine + Dishwasher load is minimised at 02:00–04:00 and 10:00–11:00.
  Worst time to run heavy appliances: 05:00–08:00 (stacks on morning surge).
"""


def _build_step_prompt(
    house_data: Dict[str, Any],
    t_in: float,
    t_target: float,
    weather_data: Dict[str, Any],
    hvac_schedule: Optional[Dict] = None,
    sim_hour: Optional[int] = None,      # FIX BUG-4: pass sim hour instead of real clock
) -> str:
    w   = _parse_weather(weather_data)
    geo = _house_geometry(house_data)
    ins = _insulation_params(house_data)

    # FIX BUG-4: use simulation hour when provided; real clock only for live app
    h    = sim_hour if sim_hour is not None else datetime.now().hour
    mins = 0       if sim_hour is not None else datetime.now().minute
    rate = get_electricity_price(h)

    scheduled_mode = "off"
    if hvac_schedule and "actions" in hvac_schedule:
        for a in hvac_schedule["actions"]:
            if a.get("hour") == h:
                scheduled_mode = a.get("mode", "off")
                break

    empirical_load_map = {
        range(2, 5):   ("LOWEST-DEMAND deep-night valley", "0.15–0.22 kW",  "ideal pre-conditioning window"),
        range(5, 9):   ("MORNING SURGE peak",              "0.40–0.43 kW",  "avoid adding HVAC load if possible"),
        range(10, 12): ("MIDDAY LULL valley",              "0.13–0.14 kW",  "second-best pre-conditioning window"),
        range(13, 17): ("AFTERNOON PEAK",                  "0.33–0.40 kW",  "coast on thermal mass — minimize runtime"),
        range(22, 24): ("LATE-NIGHT ramp-down",            "0.33–0.44 kW",  "pre-cool/pre-heat if peak coming"),
    }
    hour_context = ("standard off-peak period", "0.20–0.30 kW", "normal operation")
    for hr_range, ctx in empirical_load_map.items():
        if h in hr_range:
            hour_context = ctx
            break
    load_label, load_range, load_advice = hour_context

    if w["t_out"] < -5:
        hvac_intensity = "AGGRESSIVE heating demand expected (empirical avg 0.20–0.26 kW FurnaceHRV)"
    elif w["t_out"] < 0:
        hvac_intensity = "moderate heating demand (empirical avg 0.16 kW FurnaceHRV)"
    elif w["t_out"] < 5:
        hvac_intensity = "low heating demand (empirical avg 0.11 kW FurnaceHRV)"
    elif w["t_out"] < 15:
        hvac_intensity = "minimal HVAC demand — mild weather (empirical avg 0.09–0.13 kW)"
    elif w["t_out"] < 20:
        hvac_intensity = "transitional — light cooling may be needed (empirical avg 0.13–0.20 kW)"
    else:
        hvac_intensity = "COOLING season demand expected (empirical avg 0.19–0.23 kW FurnaceHRV)"

    # FIX BUG-3: humidity is stored as raw % (e.g. 65.0), not a fraction (0.65).
    # Was: > 0.70 (always True) and :.0% (shows 6500%). Now: > 70 and :.0f%
    humidity_note = ""
    if w["humidity"] > 70:
        humidity_note = (
            f"\n- ⚠ High humidity ({w['humidity']:.0f}%) → +12% latent load expected "
            f"(empirical). Prioritise dehumidification."
        )

    # FIX BUG-1 + BUG-2: pre-compute the required mode from the deadband rule.
    # The old template hardcoded hvac_mode="off" and new_temp_c=T_in, causing Gemini
    # to copy those defaults. The old IMPORTANT instruction biased Gemini toward "off"
    # even when T_in was far outside the deadband. Now we compute the correct mode and
    # physics ΔT up front and embed them directly in the template as the expected answer.
    deadband   = float(house_data.get("deadband_c", 1.0))
    gap        = round(t_in - t_target, 2)
    cap_heat   = _hvac_capacity(house_data, "heating")
    cap_cool   = _hvac_capacity(house_data, "cooling")
    cop_val    = _cop(w["t_out"], float(house_data.get("cop_base", 3.5)))
    c_home     = _thermal_mass(geo["floor_area_m2"])

    if t_in < t_target - deadband:
        mode_directive = "heat"
        required_label = (
            f"HEAT  — T_in={t_in:.1f}°C is {abs(gap):.2f}°C BELOW setpoint {t_target:.1f}°C "
            f"(gap > deadband ±{deadband}°C)"
        )
        cap_active  = cap_heat
        physics_dt  = round((cap_active * 1000.0 * cop_val * DT_STEP) / c_home, 3)
    elif t_in > t_target + deadband:
        mode_directive = "cool"
        required_label = (
            f"COOL  — T_in={t_in:.1f}°C is {abs(gap):.2f}°C ABOVE setpoint {t_target:.1f}°C "
            f"(gap > deadband ±{deadband}°C)"
        )
        cap_active  = cap_cool
        physics_dt  = round(-(cap_active * 1000.0 * cop_val * DT_STEP) / c_home, 3)
    else:
        mode_directive = "off"
        required_label = (
            f"OFF   — T_in={t_in:.1f}°C is within deadband ±{deadband}°C of setpoint "
            f"{t_target:.1f}°C (gap={gap:+.2f}°C)"
        )
        cap_active  = 0.0
        physics_dt  = 0.0

    expected_new_t  = round(t_in + physics_dt, 2)
    step_energy     = round(cap_active * (DT_STEP / DT_HOUR), 4)
    step_cost       = round(step_energy * rate, 4)

    return f"""{_PHYSICS_CONTEXT}

## House
- Floor area: {geo['floor_area_m2']:.0f} m²  Volume: {geo['volume_m3']:.0f} m³
- Envelope: {geo['envelope_m2']:.0f} m²  Windows: {geo['window_m2']:.0f} m²
- Insulation: {house_data.get('insulation_quality','average')}  U={ins['u_value']} W/m²K
- HVAC: {house_data.get('hvac_type','central')}
- Heating cap: {cap_heat} kW  Cooling cap: {cap_cool} kW

## Current Conditions ({h:02d}:{mins:02d})
- T_in={t_in:.1f}°C  T_target={t_target:.1f}°C  T_out={w['t_out']}°C
- Humidity={w['humidity']:.0f}%  Wind={w['wind_ms']} m/s  Solar={w['radiation']} W/m²
- Precip={w['precip']} mm  Snow={w['snow_depth']} m
- Scheduled mode: {scheduled_mode}  Rate: ${rate}/kWh{humidity_note}

## ★ Empirical Context for Hour {h:02d}:00
- This hour is a {load_label} (historical avg {load_range}).
- Advice: {load_advice}.
- T_out={w['t_out']:.1f}°C → {hvac_intensity}

## Deadband Analysis — MANDATORY: your hvac_mode MUST match this result
- Rule: turn ON if |T_in − T_target| > ±{deadband}°C; turn OFF if |gap| < 0.3°C
- Current gap: T_in − T_target = {gap:+.2f}°C  (deadband = ±{deadband}°C)
- ▶ REQUIRED MODE: {required_label}
- Physics ΔT this step with mode={mode_directive}: {physics_dt:+.3f}°C
  (RC model: COP={cop_val:.2f}, cap={cap_active:.2f} kW, C_home={c_home/1e6:.1f} MJ/°C)

## Output — JSON only, no other text.
Fill in the values below. The physics estimates are provided — refine them if your
analysis of the full heat-balance (solar, wind, conduction) justifies a different ΔT,
but hvac_mode MUST match the REQUIRED MODE above.
{{
  "new_temp_c": {expected_new_t},
  "hvac_mode": "{mode_directive}",
  "hvac_power_kw": {cap_active},
  "hvac_energy_kwh": {step_energy},
  "electricity_rate": {rate},
  "cost_this_step": {step_cost},
  "should_run_hvac": {str(mode_directive != "off").lower()},
  "reason": "deadband: gap={gap:+.2f}°C vs ±{deadband}°C → mode={mode_directive}. {load_label}. T_out={w['t_out']:.1f}°C ({hvac_intensity[:50]})"
}}"""


def _build_schedule_prompt(
    house_data: Dict[str, Any],
    weather_rows: List[Dict],
    t_in: float,
    t_target: float,
    sim_hour: Optional[int] = None,      # FIX BUG-4: use sim hour, not real clock
) -> str:
    # FIX BUG-4: use simulation hour when provided; real clock only for live app
    h    = sim_hour if sim_hour is not None else datetime.now().hour
    mins = 0       if sim_hour is not None else datetime.now().minute
    geo  = _house_geometry(house_data)
    ins  = _insulation_params(house_data)

    lines = []
    for i in range(24):
        hour = (h + i) % 24
        row  = weather_rows[min(i, len(weather_rows)-1)]
        w    = _parse_weather(row)
        p    = get_electricity_price(hour)
        tier = "OFF" if p <= _TOU_OFF_PEAK else "MID" if p <= _TOU_MID_PEAK else "ON"
        # ★ Tag each hour with its empirical load label so AI can see at a glance
        if hour in range(5, 9):
            emp_tag = "↑SURGE"
        elif hour in range(13, 17):
            emp_tag = "↑PEAK"
        elif hour in range(2, 5) or hour in range(10, 12):
            emp_tag = "↓VALLEY"
        else:
            emp_tag = ""
        lines.append(
            f"  {hour:02d}:00  T_out={w['t_out']}°C  solar={w['radiation']}W/m²"
            f"  ${p} ({tier}) {emp_tag}"
        )

    cap_h = _hvac_capacity(house_data, "heating")
    cap_c = _hvac_capacity(house_data, "cooling")

    # Identify the best pre-conditioning windows in the upcoming 24 h
    upcoming_valleys = [
        f"{(h+i)%24:02d}:00"
        for i in range(24)
        if (h+i)%24 in list(range(2,5)) + list(range(10,12))
    ][:4]

    # Identify the peaks the schedule must navigate
    upcoming_peaks = [
        f"{(h+i)%24:02d}:00–{(h+i+1)%24:02d}:00"
        for i in range(24)
        if (h+i)%24 in list(range(5,9)) + list(range(13,17))
    ][:6]

    return f"""{_PHYSICS_CONTEXT}

## House
- Area: {geo['floor_area_m2']:.0f} m²  HVAC: {house_data.get('hvac_type','central')}
- Insulation: {house_data.get('insulation_quality','average')}  U={ins['u_value']}
- Heating: {cap_h} kW  Cooling: {cap_c} kW
- T_target={t_target}°C  deadband=±{house_data.get('deadband_c',1.0)}°C
- Current T_in={t_in}°C

## 24-Hour Forecast starting hour {h:02d} (↑SURGE/↑PEAK = high-load, ↓VALLEY = low-load)
{chr(10).join(lines)}

## ★ Empirical Scheduling Rules (derived from 8,760 hrs of real data)

### Pre-Conditioning Windows (schedule HVAC start here to front-load cheap energy):
  Best slots in next 24 h: {', '.join(upcoming_valleys) if upcoming_valleys else 'none in window'}
  → Use these to build thermal buffer BEFORE the morning surge (05:00–08:00)
    and afternoon peak (13:00–16:00).

### Peak-Avoidance Windows (HVAC should be OFF or minimal here):
  High-load peaks: {', '.join(upcoming_peaks) if upcoming_peaks else 'none in window'}
  → Historical data shows these overlap with Ontario TOU ON/MID-peak pricing.
    Let thermal mass coast. Only run HVAC if T_in exits the deadband.

### Season-Specific Insights:
  WINTER (T_out < 5°C):
    - Heaviest furnace demand at 04:00–06:00 (empirical avg 0.28–0.41 kW).
    - Pre-heat during 02:00–04:00 off-peak to build thermal buffer.
    - House typically loses 1–2°C/hr at −10°C outdoor without HVAC.
  SUMMER (T_out > 18°C):
    - Peak cooling loads 07:00–08:00 and 14:00–16:00 (0.46–0.50 kW).
    - High humidity (avg 70%, max 98%) adds +12% latent load.
    - Pre-cool to T_target−1.5°C before 07:00 and 13:00 to coast peaks.
  SHOULDER (5°C ≤ T_out ≤ 18°C):
    - Minimal HVAC needed; empirical avg only 0.09–0.13 kW.
    - Favour "off" mode; only activate if T_in drifts > deadband.

### Appliance Co-scheduling (embed in recommendations field):
  WashingMachine & Dishwasher BEST at: 10:00–12:00 or 02:00–04:00
  AVOID scheduling heavy appliances at: 05:00–08:00 (stacks on morning surge)

## Strategy
1. Identify pre-conditioning windows using VALLEY tags above.
2. Schedule HVAC start so T_in reaches T_target by the time peaks begin.
3. Coast through SURGE/PEAK hours on thermal mass.
4. IMPORTANT: start_time and end_time must include realistic MINUTES, not just hours.
   E.g. "04:37" not "04:00". Think about when T_in actually drifts past the
   deadband (±{house_data.get("deadband_c",1.0)}°C around {t_target}°C) given thermal mass.

## Output (JSON only — run cycles, not fixed hourly slots):
{{
  "actions": [
    {{"hour":{h},"mode":"heat|cool|pre-heat|pre-cool|off",
      "start_time":"{h:02d}:{mins:02d}",
      "end_time":"HH:MM",
      "power_kw":0.0,"cost":0.0,
      "reason":"reference empirical pattern + why HVAC turns on/off at this exact time",
      "predicted_temp_c":{t_in},"target_temp_c":{t_target}}}
  ],
  "total_cost":0.0,"total_energy_kwh":0.0,
  "comfort_score":95.0,"strategy_summary":"..."
}}"""


def _call_genai(prompt: str) -> Optional[Dict]:
    global _CB_DISABLED_UNTIL

    if not (_GENAI_AVAILABLE and GENAI_KEY):
        return None

    now = datetime.now()
    if _CB_DISABLED_UNTIL is not None:
        if now < _CB_DISABLED_UNTIL:
            return None
        else:
            _CB_DISABLED_UNTIL = None

    try:
        model    = genai.GenerativeModel("gemini-2.5-flash-lite")
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                max_output_tokens=4096,
                response_mime_type="application/json",
            )
        )
        text = response.text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = re.sub(r',\s*([}\]])', r'\1', text)

        for attempt in (text, text[text.find('{'):text.rfind('}')+1]):
            try:
                return json.loads(attempt)
            except (json.JSONDecodeError, ValueError):
                pass

    except Exception as exc:
        err_str = str(exc)
        if "429" in err_str or "quota" in err_str.lower() or "RESOURCE_EXHAUSTED" in err_str:
            _CB_DISABLED_UNTIL = datetime.now() + timedelta(seconds=_CB_COOLDOWN_SECONDS)
            mins = _CB_COOLDOWN_SECONDS // 60
            print(
                f"[GenAI] Quota exceeded — circuit breaker tripped. "
                f"Switching to RC physics engine for {mins} minutes."
            )
        else:
            print(f"[GenAI] {exc}")
    return None


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 5b — AI Setpoint Optimiser
# ════════════════════════════════════════════════════════════════════════════

_SETPOINT_REFRESH_INTERVAL_S: float = 1800.0


def _build_setpoint_prompt(
    house_data:      Dict[str, Any],
    t_in:            float,
    comfort_c:       float,
    comfort_tol:     float,
    weather_rows:    List[Dict],
    current_hour:    int,
) -> str:
    # ── ★ ENHANCED: setpoint prompt gives the AI empirical thresholds so it
    #    can make data-grounded pre-conditioning decisions, not guesses ──────
    lines = []
    for i in range(min(24, len(weather_rows))):
        h   = (current_hour + i) % 24
        w   = _parse_weather(weather_rows[i])
        p   = get_electricity_price(h)
        tier = "OFF" if p <= _TOU_OFF_PEAK else "MID" if p <= _TOU_MID_PEAK else "ON"
        if h in range(5, 9):
            emp_tag = "↑MORNING SURGE (0.40–0.43 kW hist.)"
        elif h in range(13, 17):
            emp_tag = "↑AFTERNOON PEAK (0.33–0.40 kW hist.)"
        elif h in range(2, 5):
            emp_tag = "↓BEST PRE-COND WINDOW (0.15–0.22 kW hist.)"
        elif h in range(10, 12):
            emp_tag = "↓MIDDAY VALLEY (0.13–0.14 kW hist.)"
        else:
            emp_tag = ""
        lines.append(
            f"  {h:02d}:00  T_out={w['t_out']:.1f}°C  solar={w['radiation']:.0f}W/m²"
            f"  ${p} ({tier})  {emp_tag}"
        )

    geo  = _house_geometry(house_data)
    ins  = _insulation_params(house_data)
    cap_h = _hvac_capacity(house_data, "heating")
    cap_c = _hvac_capacity(house_data, "cooling")
    thermal_mass = _thermal_mass(geo["floor_area_m2"])

    thermal_inertia_factor = min(1.0, max(0.2, thermal_mass / 5e6))
    insulation_factor = min(1.0, max(0.3, 0.5 / ins["u_value"]))
    drift_capacity = (thermal_inertia_factor + insulation_factor) / 2.0
    max_safe_drift = comfort_tol * drift_capacity

    # Classify current outdoor temp against empirical thresholds
    t_out_now = _parse_weather(weather_rows[0])["t_out"] if weather_rows else 15.0
    if t_out_now < -5:
        empirical_hvac_demand = "AGGRESSIVE heating (empirical: 0.20–0.26 kW FurnaceHRV). Winter morning 04:00–06:00 is the hardest peak — pre-heat NOW if in that window."
    elif t_out_now < 5:
        empirical_hvac_demand = "moderate heating demand (empirical: 0.09–0.16 kW). Pre-heat during 02:00–04:00 valley if upcoming surge detected."
    elif t_out_now < 15:
        empirical_hvac_demand = "minimal HVAC demand (empirical: 0.09–0.13 kW). Hold comfort setpoint; no aggressive pre-conditioning needed."
    elif t_out_now < 22:
        empirical_hvac_demand = "transitional cooling (empirical: 0.13–0.20 kW). Pre-cool during 10:00–12:00 valley ahead of afternoon peak (14:00–16:00 = 0.46–0.50 kW)."
    else:
        empirical_hvac_demand = "ACTIVE cooling season (empirical: 0.19–0.23 kW). High humidity likely (+12% latent load). Pre-cool to T_target−1.5°C during 02:00–04:00 or 10:00–12:00."

    # Humidity alert
    hum_now = _parse_weather(weather_rows[0]).get("humidity", 50) if weather_rows else 50
    humidity_advisory = ""
    if hum_now > 70:
        humidity_advisory = f"\n⚠ HUMIDITY ALERT: Current RH={hum_now:.0f}% exceeds 70% empirical threshold. Expect +12% latent dehumidification load. Adjust setpoint slightly cooler to reduce latent burden."

    return f"""{_PHYSICS_CONTEXT}

## Task
Choose a single OPTIMAL thermostat setpoint for RIGHT NOW (hour {current_hour:02d}).
The controller will chase this setpoint; it will call again in ~30 min for a new value.
⚠️ YOUR RECOMMENDATIONS MUST BE GROUNDED IN THE EMPIRICAL PATTERNS BELOW.

## Comfort Constraints  ← HARD LIMITS, never violate
- User comfort centre: {comfort_c:.1f}°C
- Allowed comfort band: [{comfort_c - comfort_tol:.1f}, {comfort_c + comfort_tol:.1f}] °C
- Your setpoint MUST stay within this band.

## House Physical Properties
- Floor area: {geo['floor_area_m2']:.0f} m²
- Insulation quality: {house_data.get('insulation_quality','average')}  (U-value: {ins['u_value']} W/m²K)
- Thermal mass: {thermal_mass/1e6:.1f} MJ/°C
  └─ Higher thermal mass = slower temperature drift = can pre-condition more aggressively
- HVAC: {house_data.get('hvac_type','central')}  Heat:{cap_h}kW / Cool:{cap_c}kW
- COP baseline: {house_data.get('cop_base', 3.5)}
- Safe drift capacity for this house: ±{max_safe_drift:.1f}°C during peak hours

## Current Indoor State
- T_in now: {t_in:.1f}°C
- Deadband: ±{house_data.get('deadband_c', 1.0)}°C{humidity_advisory}

## ★ Empirical HVAC Demand at Current Outdoor Temp ({t_out_now:.1f}°C)
{empirical_hvac_demand}

## 24-Hour Forecast (↑ = high-load peak, ↓ = pre-conditioning opportunity)
{chr(10).join(lines)}

## ★ Empirical Setpoint Decision Rules (data-grounded):

### Rule 1 — Pre-Conditioning (OFF-PEAK + peak ahead within 2 hrs)
  WINTER: Raise setpoint to comfort_c + {max_safe_drift:.1f}°C (pre-heat buffer)
    → Empirical basis: winter morning peak 04:00–06:00 hits 0.28–0.41 kW.
      Pre-heating at 02:00–04:00 off-peak avoids 30–40% of peak HVAC cost.
  SUMMER: Lower setpoint to comfort_c − {max_safe_drift:.1f}°C (pre-cool buffer)
    → Empirical basis: summer cooling peaks 07:00–08:00 (0.46–0.50 kW).
      Pre-cooling during 10:00–12:00 valley shifts load by ~2–3 hrs.

### Rule 2 — Peak Hours (ON-PEAK, T_in still in comfort band)
  Let setpoint drift toward the opposite edge of the comfort band.
  → COOLING season: allow up to comfort_c + {max_safe_drift:.1f}°C (drift warm)
  → HEATING season: allow down to comfort_c − {max_safe_drift:.1f}°C (drift cool)
  → Empirical basis: thermal mass sustains ±{max_safe_drift:.1f}°C drift for
    60–90 min in a well-insulated home before requiring HVAC intervention.

### Rule 3 — Mild Weather (5°C ≤ T_out ≤ 15°C, empirical avg 0.09–0.13 kW)
  No pre-conditioning needed. Hold comfort_c exactly.
  HVAC runtime is minimal at these outdoor temperatures in historical data.

### Rule 4 — Humidity Override (RH > 70%)
  In summer, high humidity adds +12% latent load.
  Lower setpoint by 0.5°C below comfort_c to account for latent dehumidification.

## Optimisation Goal
Minimize J = Σ P_HVAC(t) · Price(t)
While keeping T_in in [{comfort_c - comfort_tol:.1f}, {comfort_c + comfort_tol:.1f}] °C at all times.

## Output — JSON ONLY, no other text:
{{
  "optimal_setpoint_c": {comfort_c:.1f},
  "strategy": "Cite empirical pattern + specific reason for this exact setpoint value",
  "expected_cost_saving_pct": 0.0,
  "next_review_minutes": 30,
  "pre_conditioning": false,
  "comfort_impact": "none | slight | moderate"
}}"""


def _optimize_setpoint_physics(
    house_data:   Dict[str, Any],
    t_in:         float,
    comfort_c:    float,
    comfort_tol:  float,
    weather_rows: List[Dict],
    current_hour: int,
) -> Dict[str, Any]:
    horizon = min(6, len(weather_rows))
    prices_ahead = [get_electricity_price((current_hour + i) % 24) for i in range(horizon)]
    current_price = prices_ahead[0]

    t_out_avg = sum(
        _parse_weather(weather_rows[min(i, len(weather_rows) - 1)])["t_out"]
        for i in range(horizon)
    ) / max(horizon, 1)

    cooling_season = t_out_avg >= comfort_c

    max_future_price = max(prices_ahead[1:4]) if len(prices_ahead) > 3 else current_price
    peak_coming      = max_future_price > current_price + 0.01

    geo = _house_geometry(house_data)
    ins = _insulation_params(house_data)
    c_home = _thermal_mass(geo["floor_area_m2"])
    thermal_inertia_factor = min(1.0, max(0.2, c_home / 5e6))
    insulation_factor = min(1.0, max(0.3, 0.5 / ins["u_value"]))
    drift_capacity = (thermal_inertia_factor + insulation_factor) / 2.0

    cap_heat = _hvac_capacity(house_data, "heating")
    cap_cool = _hvac_capacity(house_data, "cooling")
    hvac_power = (cap_heat + cap_cool) / 2.0
    recovery_factor = min(1.0, hvac_power / 5.0)

    pre_shift   = comfort_tol * (0.50 + 0.35 * drift_capacity)
    coast_shift = comfort_tol * (0.50 + 0.40 * drift_capacity)

    if current_price <= _TOU_OFF_PEAK:
        if peak_coming:
            if cooling_season:
                setpoint = comfort_c - pre_shift
                strategy = (
                    f"🧊 Pre-cooling to {comfort_c - pre_shift:.1f}°C off-peak (${current_price}/kWh) "
                    f"ahead of ${max_future_price:.3f}/kWh peak. "
                    f"[Your {house_data.get('insulation_quality','average')} home can safely drift {comfort_tol * drift_capacity:.1f}°C "
                    f"without exceeding comfort band]"
                )
                pre_cond = True
            else:
                setpoint = comfort_c + pre_shift
                strategy = (
                    f"🔥 Pre-heating to {comfort_c + pre_shift:.1f}°C off-peak (${current_price}/kWh) "
                    f"ahead of ${max_future_price:.3f}/kWh peak. "
                    f"[Your {house_data.get('insulation_quality','average')} home with {house_data.get('hvac_type','central')} HVAC "
                    f"can safely drift {comfort_tol * drift_capacity:.1f}°C]"
                )
                pre_cond = True
        else:
            setpoint = comfort_c
            strategy = f"☑️ Off-peak (${current_price}/kWh), no peak imminent — holding your comfort {comfort_c:.1f}°C."
            pre_cond = False

    elif current_price >= _TOU_ON_PEAK:
        if cooling_season:
            setpoint = comfort_c + coast_shift
            strategy = (
                f"🧠 On-peak (${current_price}/kWh) — relaxing to {comfort_c + coast_shift:.1f}°C, "
                f"coasting on thermal mass. [Your {geo['floor_area_m2']:.0f}m² {house_data.get('insulation_quality','average')} home "
                f"has {c_home/1e6:.1f} MJ/°C thermal inertia — will hold this temperature safely]"
            )
        else:
            setpoint = comfort_c - coast_shift
            strategy = (
                f"🧠 On-peak (${current_price}/kWh) — relaxing to {comfort_c - coast_shift:.1f}°C, "
                f"coasting on thermal mass. [Your {geo['floor_area_m2']:.0f}m² {house_data.get('insulation_quality','average')} home "
                f"has {c_home/1e6:.1f} MJ/°C thermal inertia — will hold safely]"
            )
        pre_cond = False

    else:
        setpoint = comfort_c
        strategy = (
            f"⚖️ Mid-peak (${current_price}/kWh) — holding your comfort {comfort_c:.1f}°C. "
            f"[With your {house_data.get('hvac_type','central')} system, "
            f"the balance favors staying at your preferred temperature.]"
        )
        pre_cond = False

    setpoint = max(comfort_c - comfort_tol, min(comfort_c + comfort_tol, setpoint))

    saving_pct = 15.0 if pre_cond else 0.0

    drift_c = setpoint - comfort_c
    band_min = round(comfort_c - comfort_tol, 1)
    band_max = round(comfort_c + comfort_tol, 1)

    if abs(drift_c) < 0.1:
        drift_reason = f"User comfort centre is {comfort_c:.1f}°C (±{comfort_tol:.1f}°C band = {band_min}–{band_max}°C). AI suggests {setpoint:.1f}°C (drift: {drift_c:+.1f}°C). No adjustment ✓"
    elif abs(drift_c) <= comfort_tol:
        drift_reason = f"User comfort centre is {comfort_c:.1f}°C (±{comfort_tol:.1f}°C band = {band_min}–{band_max}°C). AI suggests {setpoint:.1f}°C (drift: {drift_c:+.1f}°C). Within safe comfort band ✓"
    else:
        drift_reason = f"User comfort centre is {comfort_c:.1f}°C (±{comfort_tol:.1f}°C band = {band_min}–{band_max}°C). AI suggests {setpoint:.1f}°C (drift: {drift_c:+.1f}°C). WARNING: Outside band ✗"

    return {
        "optimal_setpoint_c":       round(setpoint, 1),
        "comfort_center_c":         round(comfort_c, 1),
        "comfort_tolerance_c":      round(comfort_tol, 1),
        "comfort_band_min_c":       band_min,
        "comfort_band_max_c":       band_max,
        "drift_from_comfort_c":     round(drift_c, 1),
        "drift_reason":             drift_reason,
        "strategy":                  strategy,
        "expected_cost_saving_pct":  saving_pct,
        "next_review_minutes":       30,
        "pre_conditioning":          pre_cond,
        "comfort_impact":            "slight" if pre_cond else "none",
        "source":                    "physics",
    }


def optimize_setpoint_ai(
    house_data:    Dict[str, Any],
    t_in:          float,
    weather_rows:  List[Dict],
    comfort_c:     float = 22.0,
    comfort_tol:   float = 2.0,
    sim_hour:      Optional[int] = None,   # FIX BUG-4: use sim hour for TOU context
) -> Dict[str, Any]:
    comfort_c   = max(15.0, min(30.0, float(comfort_c)))
    comfort_tol = max(0.5,  min(4.0,  float(comfort_tol)))
    t_in        = max(5.0,  min(40.0, float(t_in)))

    # FIX BUG-4: use simulation hour when provided; real clock for live app
    current_hour = sim_hour if sim_hour is not None else datetime.now().hour

    band_min = round(comfort_c - comfort_tol, 1)
    band_max = round(comfort_c + comfort_tol, 1)

    if not weather_rows:
        return {
            "optimal_setpoint_c":       comfort_c,
            "comfort_center_c":         round(comfort_c, 1),
            "comfort_tolerance_c":      round(comfort_tol, 1),
            "comfort_band_min_c":       band_min,
            "comfort_band_max_c":       band_max,
            "drift_from_comfort_c":     0.0,
            "drift_reason":             f"No weather forecast. Using comfort centre {comfort_c:.1f}°C.",
            "strategy":                  "No weather forecast available — using comfort setpoint.",
            "expected_cost_saving_pct":  0.0,
            "next_review_minutes":       30,
            "pre_conditioning":          False,
            "comfort_impact":            "none",
            "source":                    "physics",
        }

    prompt = _build_setpoint_prompt(
        house_data, t_in, comfort_c, comfort_tol, weather_rows, current_hour
    )
    result = _call_genai(prompt)

    if result is not None and "optimal_setpoint_c" in result:
        sp = float(result["optimal_setpoint_c"])
        sp = max(comfort_c - comfort_tol, min(comfort_c + comfort_tol, sp))
        drift_c = sp - comfort_c

        result["optimal_setpoint_c"] = round(sp, 1)
        result["comfort_center_c"] = round(comfort_c, 1)
        result["comfort_tolerance_c"] = round(comfort_tol, 1)
        result["comfort_band_min_c"] = band_min
        result["comfort_band_max_c"] = band_max
        result["drift_from_comfort_c"] = round(drift_c, 1)
        result.setdefault("drift_reason",
            f"User comfort centre is {comfort_c:.1f}°C (±{comfort_tol:.1f}°C band = {band_min}–{band_max}°C). "
            f"AI suggests {sp:.1f}°C (drift: {drift_c:+.1f}°C). Within safe comfort band ✓"
        )
        result.setdefault("source", "genai")
        result.setdefault("pre_conditioning", False)
        result.setdefault("comfort_impact", "none")
        result.setdefault("next_review_minutes", 30)
        return result

    return _optimize_setpoint_physics(
        house_data, t_in, comfort_c, comfort_tol, weather_rows, current_hour
    )


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 5c — Physics Energy Floor (BUG-5 helper)
# ════════════════════════════════════════════════════════════════════════════

def _compute_physics_energy_floor(
    house_data:   Dict[str, Any],
    weather_rows: List[Dict],
    t_setpoint:   float = 22.0,
) -> float:
    """
    Compute the minimum physically plausible electricity consumption over 24 h.

    Methodology: sum the hourly heat loss through walls + infiltration at the
    given outdoor temps, divide by average COP.  This is the absolute minimum
    the HVAC must consume just to maintain the setpoint — any AI schedule
    claiming less than 20% of this floor on a cold day is hallucinated.

    Returns 0.0 for warm days (no heating required).
    """
    geo = _house_geometry(house_data)
    ins = _insulation_params(house_data)
    cop_base = float(house_data.get("cop_base", 3.5))

    total_loss_kw = 0.0
    t_out_sum     = 0.0
    n_cold_hours  = 0

    for row in weather_rows[:24]:
        w     = _parse_weather(row)
        t_out = w["t_out"]
        t_out_sum += t_out
        if t_out >= t_setpoint:
            continue                          # no heating loss this hour
        q_walls = abs(_q_conductive(ins["u_value"], geo["envelope_m2"], t_out, t_setpoint))
        q_infil = abs(_q_wind(geo["volume_m3"], ins["ach_base"], 0.02,
                              w["wind_ms"], t_out, t_setpoint))
        total_loss_kw += (q_walls + q_infil) / 1000.0
        n_cold_hours  += 1

    if n_cold_hours == 0:
        return 0.0

    avg_t_out = t_out_sum / 24
    cop       = max(1.0, _cop(avg_t_out, cop_base))
    # total_loss_kw is summed over n_cold_hours; ÷ cop gives electricity kWh
    return max(0.0, total_loss_kw / cop)


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — Data Classes
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class HVACAction:
    hour: int
    mode: str
    start_time: str
    end_time: str
    power_kw: float
    cost: float
    reason: str
    predicted_temp_c: float
    target_temp_c: float


@dataclass
class HVACSchedule:
    actions: List[HVACAction]
    total_cost: float
    total_energy_kwh: float
    comfort_score: float
    generated_at: str

    def to_dict(self) -> dict:
        return {
            "actions":          [asdict(a) for a in self.actions],
            "total_cost":       self.total_cost,
            "total_energy_kwh": self.total_energy_kwh,
            "comfort_score":    self.comfort_score,
            "generated_at":     self.generated_at,
        }


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — Public API (called by indoor_temperature.py)
# ════════════════════════════════════════════════════════════════════════════

def simulate_step_with_hvac(
    house_data:           Dict[str, Any],
    current_indoor_temp_c: float,
    target_temp_c:        Optional[float] = None,
    weather_data:         Dict[str, Any] = None,
    hvac_schedule:        Optional[Dict] = None,
    personal_comfort:     Optional[float] = None,
    dt_s:                 float = DT_STEP,
    sim_hour:             Optional[int] = None,   # FIX BUG-4: pass to prompt builder
) -> Dict[str, Any]:
    if target_temp_c is None:
        target_temp_c = house_data.get("personal_comfort", 22.0)
    if personal_comfort is None:
        personal_comfort = house_data.get("personal_comfort", 22.0)

    t_in     = max(5.0,  min(40.0, float(current_indoor_temp_c)))
    t_target = max(15.0, min(30.0, float(target_temp_c)))

    if dt_s >= 30:
        # FIX BUG-4: pass sim_hour so the prompt uses the right TOU / load context
        prompt = _build_step_prompt(
            house_data, t_in, t_target, weather_data, hvac_schedule,
            sim_hour=sim_hour,
        )
        result = _call_genai(prompt)

        if result is not None:
            new_t     = float(result.get("new_temp_c", t_in))
            raw_delta = new_t - t_in

            # FIX BUG-1 safety net: if Gemini returned mode=off AND ΔT≈0 while the
            # gap clearly exceeds the deadband, it copied the old template default.
            # Fall through to the physics engine instead of returning a wrong answer.
            deadband      = float(house_data.get("deadband_c", 1.0))
            gap           = abs(t_in - t_target)
            returned_mode = result.get("hvac_mode", "off")
            template_copy = (abs(raw_delta) < 0.001 and returned_mode == "off"
                             and gap > deadband)
            if template_copy:
                print(
                    f"[GenAI] Step result rejected — looks like a template copy "
                    f"(mode=off, ΔT=0 but gap={gap:.2f}°C > deadband={deadband}°C). "
                    f"Falling back to RC physics."
                )
            else:
                scaled_delta = raw_delta * (dt_s / DT_STEP)
                max_delta    = 0.5 * (dt_s / DT_STEP)
                scaled_delta = max(-max_delta, min(max_delta, scaled_delta))
                new_t        = max(5.0, min(40.0, t_in + scaled_delta))
                result["new_temp_c"] = round(new_t, 2)
                result.setdefault("engine", "genai")
                return result

    return _physics_step(
        house_data   = house_data,
        t_in         = t_in,
        t_target     = t_target,
        weather_data = weather_data,
        dt_s         = dt_s,
    )


def generate_hvac_schedule(
    house_data:           Dict[str, Any],
    weather_rows:         List[Dict],
    current_indoor_temp_c: float,
    personal_comfort:     Optional[float] = None,
    target_temp_c:        Optional[float] = None,
    sim_hour:             Optional[int] = None,   # FIX BUG-4: pass to prompt builder
) -> HVACSchedule:
    if target_temp_c is None:
        target_temp_c = house_data.get("personal_comfort", 22.0)
    if personal_comfort is None:
        personal_comfort = house_data.get("personal_comfort", 22.0)

    t_target     = max(15.0, min(30.0, float(target_temp_c)))
    t_in         = max(5.0,  min(40.0, float(current_indoor_temp_c)))
    current_hour = sim_hour if sim_hour is not None else datetime.now().hour

    # FIX BUG-4: pass sim_hour to prompt builder
    prompt = _build_schedule_prompt(house_data, weather_rows, t_in, t_target,
                                    sim_hour=sim_hour)
    result = _call_genai(prompt)

    if result is not None and "actions" in result and len(result["actions"]) >= 12:
        # FIX BUG-5: validate energy is physically plausible before trusting the schedule.
        # Compute the minimum electricity the house MUST consume to offset heat loss.
        # If Gemini claims less than 20% of that floor it is hallucinating.
        ai_kwh        = float(result.get("total_energy_kwh", 0))
        physics_floor = _compute_physics_energy_floor(house_data, weather_rows, t_target)
        schedule_plausible = not (physics_floor > 2.0 and ai_kwh < physics_floor * 0.20)

        if not schedule_plausible:
            print(
                f"[GenAI] Schedule rejected — claimed {ai_kwh:.2f} kWh but physics "
                f"floor is {physics_floor:.1f} kWh (AI < 20% of minimum). "
                f"Falling back to RC physics schedule."
            )
        else:
            actions = []
            for a in result["actions"]:
                actions.append(HVACAction(
                    hour             = int(a.get("hour", 0)),
                    mode             = a.get("mode", "off"),
                    start_time       = a.get("start_time", "00:00"),
                    end_time         = a.get("end_time",   "01:00"),
                    power_kw         = float(a.get("power_kw", 0)),
                    cost             = float(a.get("cost", 0)),
                    reason           = a.get("reason", ""),
                    predicted_temp_c = float(a.get("predicted_temp_c", t_target)),
                    target_temp_c    = t_target,
                ))
            while len(actions) < 24:
                h = (current_hour + len(actions)) % 24
                actions.append(HVACAction(
                    hour=h, mode="off",
                    start_time=f"{h:02d}:00", end_time=f"{(h+1)%24:02d}:00",
                    power_kw=0.0, cost=0.0, reason="No action scheduled",
                    predicted_temp_c=t_target, target_temp_c=t_target,
                ))
            return HVACSchedule(
                actions          = actions[:24],
                total_cost       = round(float(result.get("total_cost", 0)), 3),
                total_energy_kwh = round(float(result.get("total_energy_kwh", 0)), 2),
                comfort_score    = float(result.get("comfort_score", 85)),
                generated_at     = datetime.now().isoformat(),
            )

    sched_dict = _physics_schedule(
        house_data, weather_rows, t_in, t_target,
        current_hour, datetime.now().minute if sim_hour is None else 0
    )
    _action_fields = {f.name for f in HVACAction.__dataclass_fields__.values()}
    actions = [
        HVACAction(**{k: v for k, v in a.items() if k in _action_fields})
        for a in sched_dict["actions"]
    ]
    return HVACSchedule(
        actions          = actions,
        total_cost       = sched_dict["total_cost"],
        total_energy_kwh = sched_dict["total_energy_kwh"],
        comfort_score    = sched_dict["comfort_score"],
        generated_at     = sched_dict["generated_at"],
    )


def predict_temperature(
    house_data:          Dict[str, Any],
    current_indoor_temp_c: float,
    weather_data:        Dict[str, Any],
    hvac_mode:           str = "off",
    target_temp_c:       Optional[float] = None,
    timestep_minutes:    int = 5,
) -> Dict[str, Any]:
    return simulate_step_with_hvac(
        house_data            = house_data,
        current_indoor_temp_c = current_indoor_temp_c,
        target_temp_c         = target_temp_c,
        weather_data          = weather_data,
    )


# ── Helper accessors ──────────────────────────────────────────────────────────

def _parse_hhmm(t: str) -> int:
    try:
        parts = t.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def get_current_hvac_action(schedule: HVACSchedule) -> Optional[HVACAction]:
    now = datetime.now()
    now_mins = now.hour * 60 + now.minute
    for a in schedule.actions:
        if a.start_time and a.end_time:
            start = _parse_hhmm(a.start_time)
            end   = _parse_hhmm(a.end_time)
            if start <= end:
                if start <= now_mins < end:
                    return a
            else:
                if now_mins >= start or now_mins < end:
                    return a
    return next((a for a in schedule.actions if a.hour == now.hour), None)


def get_upcoming_actions(schedule: HVACSchedule, count: int = 5) -> List[HVACAction]:
    now      = datetime.now()
    now_mins = now.hour * 60 + now.minute

    def mins_until(a: HVACAction) -> int:
        start = _parse_hhmm(a.start_time) if a.start_time else a.hour * 60
        diff  = (start - now_mins) % (24 * 60)
        return diff

    active_or_future = [
        a for a in schedule.actions
        if a.mode != "off" and mins_until(a) <= 12 * 60
    ]
    return sorted(active_or_future, key=mins_until)[:count]