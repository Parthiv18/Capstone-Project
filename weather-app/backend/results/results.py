"""
=============================================================================
  HVAC Capstone — Gemini AI vs Physics Comparison Script
  ELE 70A / 70B  ·  Toronto Metropolitan University
=============================================================================
Tests all three Gemini-powered functions against the RC physics engine
across 8 weather scenarios.  For each scenario the script measures:

  Test 1  simulate_step_with_hvac()  — single 5-min step decision
  Test 2  generate_hvac_schedule()   — full 24-hour HVAC plan
  Test 3  optimize_setpoint_ai()     — smart thermostat setpoint

Usage (run from the weather-app/ directory):
    export GENAI_KEY="your-google-gemini-api-key"
    python track_results_ai.py              # full run, saves report
    python track_results_ai.py --quick      # 2 scenarios only
    python track_results_ai.py --scenario hot_summer_day
    python track_results_ai.py --api-key YOUR_KEY
    python track_results_ai.py --no-save    # print only, no file

Output:
    ai_results_report.txt  — full human-readable comparison report
=============================================================================
"""

import sys
import os
import math
import time
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

# ── Path setup ────────────────────────────────────────────────────────────────
OUT_DIR      = Path(__file__).resolve().parent
BACKEND_ROOT = OUT_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
_backend_sub = OUT_DIR / "backend"
if _backend_sub.exists() and str(_backend_sub) not in sys.path:
    sys.path.insert(0, str(_backend_sub))

# ── Allow --api-key before importing hvac_physics ────────────────────────────
_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--api-key", default=None)
_pre_args, _ = _pre.parse_known_args()
if _pre_args.api_key:
    os.environ["GENAI_KEY"] = _pre_args.api_key

from api.hvac_simulation.hvac_physics import (
    simulate_step_with_hvac,
    generate_hvac_schedule,
    optimize_setpoint_ai,
    _physics_step,
    _physics_schedule,
    _optimize_setpoint_physics,
    _compute_physics_energy_floor,
    _house_geometry,
    _insulation_params,
    _hvac_capacity,
    _cop,
    _q_conductive,
    _q_wind,
    _parse_weather,
    get_electricity_price,
    _GENAI_AVAILABLE,
    GENAI_KEY,
    DT_STEP,
    DT_HOUR,
)


# ─────────────────────────────────────────────────────────────────────────────
#  TEST HOUSE  (Toronto semi-detached, ~140 m²)
# ─────────────────────────────────────────────────────────────────────────────
HOUSE = {
    "home_size": 1500, "insulation_quality": "average", "hvac_type": "central",
    "shgc": 0.4, "k_wind": 0.02, "latent_coeff": 150.0, "humidity_target": 50.0,
    "cop_base": 3.5, "deadband_c": 1.0, "comfort_weight": 0.5, "personal_comfort": 22.0,
}


# ─────────────────────────────────────────────────────────────────────────────
#  WEATHER SCENARIOS
# ─────────────────────────────────────────────────────────────────────────────
def _diurnal(t_day, t_night, humidity, wind, solar_peak,
             precip=0.0, snowfall=0.0, snow_depth=0.0, n=48):
    rows = []
    for h in range(n):
        hour  = h % 24
        phase = math.pi * (hour - 5) / 9
        t_out = t_night + (t_day - t_night) * max(0.0, math.sin(phase))
        solar = solar_peak * max(0.0, math.sin(math.pi * (hour - 6) / 12)) if 6 <= hour <= 18 else 0.0
        rows.append({
            "temperature_2m":       round(t_out, 1),
            "relative_humidity_2m": humidity,
            "wind_speed_10m":       wind,
            "shortwave_radiation":  round(solar, 1),
            "precipitation":        precip,
            "snowfall":             snowfall,
            "snow_depth":           snow_depth,
        })
    return rows


SCENARIOS: Dict[str, Dict[str, Any]] = {
    "cold_winter_day": {
        "desc":        "Frigid winter (-18°C / -10°C), snow insulation active",
        "t_start":     18.0, "t_set": 22.0, "step_hour": 3, "season": "winter",
        "weather":     _diurnal(-10, -18, 65, 6.5, 80, snowfall=0.3, snow_depth=0.15),
        "min_kwh":     40.0, "max_kwh": 160.0, "expect_mode": "heating",
    },
    "extreme_cold": {
        "desc":        "Extreme cold stress test (-26°C / -20°C), heavy wind, heavy snow",
        "t_start":     16.0, "t_set": 22.0, "step_hour": 5, "season": "winter",
        "weather":     _diurnal(-20, -26, 70, 12.0, 40, snowfall=0.8, snow_depth=0.40),
        "min_kwh":     70.0, "max_kwh": 224.0, "expect_mode": "heating",
    },
    "mild_winter": {
        "desc":        "Mild winter (-2°C / -8°C), light wind — moderate heating",
        "t_start":     20.0, "t_set": 22.0, "step_hour": 6, "season": "winter",
        "weather":     _diurnal(-2, -8, 60, 3.0, 120, snowfall=0.1, snow_depth=0.05),
        "min_kwh":     15.0, "max_kwh": 100.0, "expect_mode": "heating",
    },
    "hot_summer_day": {
        "desc":        "Hot humid summer (33°C / 22°C), high humidity, strong solar",
        "t_start":     26.0, "t_set": 22.0, "step_hour": 14, "season": "summer",
        "weather":     _diurnal(33, 22, 78, 3.5, 750),
        "min_kwh":     15.0, "max_kwh": 78.0, "expect_mode": "cooling",
    },
    "hot_dry_summer": {
        "desc":        "Hot dry summer (35°C / 21°C), low humidity, extreme solar",
        "t_start":     27.0, "t_set": 22.0, "step_hour": 15, "season": "summer",
        "weather":     _diurnal(35, 21, 35, 2.0, 850),
        "min_kwh":     20.0, "max_kwh": 78.0, "expect_mode": "cooling",
    },
    "mild_spring_day": {
        "desc":        "Mild spring (18°C / 8°C), low humidity, decent solar",
        "t_start":     21.0, "t_set": 22.0, "step_hour": 10, "season": "spring",
        "weather":     _diurnal(18, 8, 45, 4.0, 450),
        "min_kwh":     0.0,  "max_kwh": 30.0, "expect_mode": "off_or_heat",
    },
    "rainy_fall_day": {
        "desc":        "Rainy fall (10°C / 4°C), high humidity, rain, evaporative cooling",
        "t_start":     20.0, "t_set": 22.0, "step_hour": 8, "season": "fall",
        "weather":     _diurnal(10, 4, 88, 5.0, 120, precip=2.5),
        "min_kwh":     10.0, "max_kwh": 80.0, "expect_mode": "heating",
    },
    "shoulder_season": {
        "desc":        "Shoulder season (14°C / 6°C), low humidity — minimal HVAC",
        "t_start":     22.0, "t_set": 22.0, "step_hour": 12, "season": "shoulder",
        "weather":     _diurnal(14, 6, 40, 3.0, 380),
        "min_kwh":     0.0,  "max_kwh": 25.0, "expect_mode": "off_or_heat",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
#  PHYSICAL PLAUSIBILITY CHECKER
# ─────────────────────────────────────────────────────────────────────────────
def check_plausibility(kwh: float, scenario: Dict, house: Dict) -> Dict[str, Any]:
    """
    Check whether the AI's claimed energy consumption is physically achievable.
    Uses the RC model to compute the minimum electricity the house must consume.
    """
    floor     = _compute_physics_energy_floor(house, scenario["weather"], scenario["t_set"])
    s_min     = scenario.get("min_kwh", 0)
    s_max     = scenario.get("max_kwh", 300)
    cap       = max(_hvac_capacity(house, "heating"), _hvac_capacity(house, "cooling"))
    hw_max    = cap * 24

    issues = []
    if floor > 2.0 and kwh < floor * 0.20:
        issues.append(
            f"PHYSICALLY IMPOSSIBLE — claimed {kwh:.2f} kWh but RC model requires "
            f"at least {floor * 0.20:.1f} kWh (20% of {floor:.1f} kWh heat-loss floor)"
        )
    elif s_min > 1.0 and kwh < s_min:
        issues.append(f"BELOW EXPECTED MIN — {kwh:.2f} kWh < {s_min:.1f} kWh")
    if kwh > hw_max:
        issues.append(f"EXCEEDS HARDWARE LIMIT — {kwh:.2f} kWh > {hw_max:.1f} kWh max possible")
    if kwh > s_max:
        issues.append(f"ABOVE EXPECTED MAX — {kwh:.2f} kWh > {s_max:.1f} kWh")

    return {"floor": round(floor, 2), "issues": issues, "plausible": len(issues) == 0}


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _nm(m: str) -> str:
    """Normalise mode to heat / cool / off for comparison."""
    if "heat" in str(m): return "heat"
    if "cool" in str(m): return "cool"
    return "off"


# ─────────────────────────────────────────────────────────────────────────────
#  TEST 1 — simulate_step_with_hvac  (single 5-min step)
# ─────────────────────────────────────────────────────────────────────────────
def test_step(scenario: Dict, house: Dict) -> Dict:
    """
    Calls simulate_step_with_hvac() with sim_hour so the prompt gets the
    correct TOU / load context for the scenario being tested.
    Compares result against _physics_step() on identical inputs.
    """
    hour  = scenario["step_hour"]
    w_row = scenario["weather"][min(hour, 47)]
    t_in  = float(scenario["t_start"])
    t_set = float(scenario["t_set"])
    db    = house.get("deadband_c", 1.0)
    gap   = abs(t_in - t_set)

    t0   = time.perf_counter()
    ai_r = simulate_step_with_hvac(
        house_data            = house,
        current_indoor_temp_c = t_in,
        target_temp_c         = t_set,
        weather_data          = w_row,
        dt_s                  = DT_STEP,
        sim_hour              = hour,       # passes correct hour to prompt
    )
    ai_lat = round(time.perf_counter() - t0, 2)

    ph_r = _physics_step(
        house_data   = house,
        t_in         = t_in,
        t_target     = t_set,
        weather_data = w_row,
        dt_s         = DT_STEP,
    )

    should_on  = gap > db
    ai_on      = ai_r.get("hvac_mode", "off") != "off"
    ph_on      = ph_r.get("hvac_mode", "off") != "off"
    ai_td      = round(ai_r["new_temp_c"] - t_in, 3)
    ph_td      = round(ph_r["new_temp_c"] - t_in, 3)

    return {
        "scenario":    scenario.get("name", ""),
        "t_in":        t_in, "t_set": t_set,
        "t_out":       w_row["temperature_2m"],
        "gap_c":       round(gap, 1), "db": db,
        "should_on":   should_on,

        "ai_engine":   ai_r.get("engine", "physics"),
        "ai_lat":      ai_lat,
        "ai_mode":     ai_r.get("hvac_mode", "off"),
        "ai_td":       ai_td,
        "ai_power":    ai_r.get("hvac_power_kw", 0),
        "ai_cost":     round(ai_r.get("cost_this_step", 0), 4),
        "ai_correct":  (ai_on == should_on),
        "ai_reason":   ai_r.get("reason", "")[:100],

        "ph_mode":     ph_r.get("hvac_mode", "off"),
        "ph_td":       ph_td,
        "ph_power":    ph_r.get("hvac_power_kw", 0),
        "ph_cost":     round(ph_r.get("cost_this_step", 0), 4),
        "ph_correct":  (ph_on == should_on),

        "agree":       _nm(ai_r.get("hvac_mode", "off")) == _nm(ph_r.get("hvac_mode", "off")),
        "td_diff":     round(ai_td - ph_td, 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  TEST 2 — generate_hvac_schedule  (full 24-hour plan)
# ─────────────────────────────────────────────────────────────────────────────
def test_schedule(scenario: Dict, house: Dict) -> Dict:
    """
    Calls generate_hvac_schedule() with sim_hour=0 (schedule always starts
    at midnight for consistent comparison across scenarios).
    Validates the AI energy output against the RC physics floor.
    """
    rows  = scenario["weather"][:24]
    t_in  = float(scenario["t_start"])
    t_set = float(scenario["t_set"])

    t0     = time.perf_counter()
    ai_obj = generate_hvac_schedule(
        house_data            = house,
        weather_rows          = rows,
        current_indoor_temp_c = t_in,
        target_temp_c         = t_set,
        sim_hour              = 0,          # scenario starts at midnight
    )
    ai_lat = round(time.perf_counter() - t0, 2)
    ai_s   = ai_obj.to_dict()

    ph_s = _physics_schedule(
        house_data    = house,
        weather_rows  = rows,
        t_in_initial  = t_in,
        t_set         = t_set,
        current_hour  = 0,
    )

    ai_kwh  = ai_s.get("total_energy_kwh", 0)
    ph_kwh  = ph_s.get("total_energy_kwh", 0)
    ai_cost = ai_s.get("total_cost", 0)
    ph_cost = ph_s.get("total_cost", 0)
    plaus   = check_plausibility(ai_kwh, scenario, house)

    ai_acts = ai_s.get("actions", [])
    ph_acts = ph_s.get("actions", [])
    ai_non  = [a for a in ai_acts if a.get("mode", "off") != "off"]
    ph_non  = [a for a in ph_acts if a.get("mode", "off") != "off"]
    ai_pre  = [a for a in ai_acts if a.get("mode", "") in ("pre-heat", "pre-cool")]
    ph_pre  = [a for a in ph_acts if a.get("mode", "") in ("pre-heat", "pre-cool")]

    # Mode agreement across overlapping hours
    ai_bh = {a.get("hour", -1): a.get("mode", "off") for a in ai_acts}
    ph_bh = {}
    for a in ph_acts:
        h = a.get("hour", -1)
        if h not in ph_bh:
            ph_bh[h] = a.get("mode", "off")
    common    = set(ai_bh) & set(ph_bh)
    agree_pct = round(100.0 * sum(
        1 for h in common if _nm(ai_bh[h]) == _nm(ph_bh[h])
    ) / max(len(common), 1), 1)

    return {
        "scenario":     scenario.get("name", ""),
        "t_in":         t_in, "t_set": t_set,

        "ai_engine":    "genai" if ai_lat > 0.5 else "physics",
        "ai_lat":       ai_lat,
        "ai_kwh":       round(ai_kwh, 3),
        "ai_cost":      round(ai_cost, 4),
        "ai_comfort":   ai_s.get("comfort_score", 0),
        "ai_cycles":    len(ai_non),
        "ai_pre":       len(ai_pre),
        "ai_sample":    ai_non[0].get("reason", "") if ai_non else "(all off)",

        "ph_kwh":       round(ph_kwh, 3),
        "ph_cost":      round(ph_cost, 4),
        "ph_comfort":   ph_s.get("comfort_score", 0),
        "ph_cycles":    len(ph_non),
        "ph_pre":       len(ph_pre),
        "ph_J":         ph_s.get("cost_function_J", 0),
        "ph_strategy":  ph_s.get("strategy_summary", "")[:70],

        "kwh_delta":    round(ai_kwh - ph_kwh, 3),
        "cost_delta":   round(ai_cost - ph_cost, 4),
        "ratio":        round(ai_kwh / ph_kwh, 3) if ph_kwh > 0 else 0.0,
        "agree_pct":    agree_pct,

        "plaus":        plaus,
        "plausible":    plaus["plausible"],
        "plaus_issues": plaus["issues"],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  TEST 3 — optimize_setpoint_ai  (smart thermostat setpoint)
# ─────────────────────────────────────────────────────────────────────────────
def test_setpoint(scenario: Dict, house: Dict) -> Dict:
    """
    Calls optimize_setpoint_ai() with sim_hour so TOU pricing reflects
    the scenario's simulated time, not the real server clock.
    """
    rows    = scenario["weather"][:24]
    t_in    = float(scenario["t_start"])
    comfort = float(house.get("personal_comfort", 22.0))
    tol     = 2.0
    hour    = scenario["step_hour"]
    t_out   = scenario["weather"][min(hour, 47)]["temperature_2m"]

    t0    = time.perf_counter()
    ai_sp = optimize_setpoint_ai(
        house_data   = house,
        t_in         = t_in,
        weather_rows = rows,
        comfort_c    = comfort,
        comfort_tol  = tol,
        sim_hour     = hour,                # passes correct hour for TOU context
    )
    ai_lat = round(time.perf_counter() - t0, 2)

    ph_sp = _optimize_setpoint_physics(
        house_data   = house,
        t_in         = t_in,
        comfort_c    = comfort,
        comfort_tol  = tol,
        weather_rows = rows,
        current_hour = hour,
    )

    ai_val  = ai_sp["optimal_setpoint_c"]
    ph_val  = ph_sp["optimal_setpoint_c"]
    in_band = abs(ai_val - comfort) <= tol

    return {
        "scenario":   scenario.get("name", ""),
        "t_in":       t_in, "comfort": comfort, "tol": tol,
        "hour":       hour, "t_out":   t_out,

        "ai_engine":  ai_sp.get("source", "unknown"),
        "ai_lat":     ai_lat,
        "ai_sp":      ai_val,
        "ai_drift":   ai_sp.get("drift_from_comfort_c", 0),
        "ai_pre":     ai_sp.get("pre_conditioning", False),
        "ai_saving":  ai_sp.get("expected_cost_saving_pct", 0),
        "ai_impact":  ai_sp.get("comfort_impact", ""),
        "ai_strategy":ai_sp.get("strategy", "")[:120],

        "ph_sp":      ph_val,
        "ph_drift":   ph_sp.get("drift_from_comfort_c", 0),
        "ph_pre":     ph_sp.get("pre_conditioning", False),
        "ph_saving":  ph_sp.get("expected_cost_saving_pct", 0),
        "ph_strategy":ph_sp.get("strategy", "")[:120],

        "sp_delta":   round(ai_val - ph_val, 2),
        "in_band":    in_band,
        "pre_agree":  ai_sp.get("pre_conditioning") == ph_sp.get("pre_conditioning"),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  REPORT
# ─────────────────────────────────────────────────────────────────────────────
def print_report(step_r, sched_r, setp_r, gemini_live, save_path=None):
    lines = []
    W = 84

    def p(s=""):
        lines.append(s)
        print(s)

    def hdr(t):
        p(); p("─" * W); p(f"  {t}"); p("─" * W)

    def vb(good):
        return "✓ PASS" if good else "✗ FAIL"

    def bar(v, width=28):
        filled = int(width * v / 100)
        return "█" * filled + "░" * (width - filled)

    # ── Header ────────────────────────────────────────────────────────────────
    p("=" * W)
    p("  HVAC Capstone — Gemini AI vs Physics Comparison Report")
    p(f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    p(f"  Gemini    : {'✓ LIVE — real Gemini responses' if gemini_live else '✗ NOT CONFIGURED — all results are physics (set GENAI_KEY)'}")
    p("=" * W)

    # ── What is being measured ─────────────────────────────────────────────────
    hdr("SECTION 1 — What is being measured")
    p()
    p("  Three functions are called for each scenario.")
    p("  Each is called twice: once through the Gemini routing path, and once")
    p("  directly through the RC physics engine on identical inputs.")
    p()
    p("    simulate_step_with_hvac()  → single 5-min temperature step + HVAC decision")
    p("    generate_hvac_schedule()   → full 24-hour HVAC run schedule")
    p("    optimize_setpoint_ai()     → optimal thermostat setpoint for current conditions")
    p()
    p("  Key metrics:")
    p("    ENGINE         → 'genai' = Gemini answered,  'physics' = fallback was used")
    p("    LATENCY        → seconds the Gemini call took  (physics is always < 0.01 s)")
    p("    CORRECT        → did the AI make the right on/off decision (gap vs deadband)")
    p("    PLAUSIBLE      → does the AI schedule pass the RC energy floor check")
    p("    DELTA vs PHYS  → AI value − Physics value  (0 = identical)")
    p("    IN BAND        → setpoint stayed within user's ±2°C comfort band")

    # ── Test 1: Step ──────────────────────────────────────────────────────────
    hdr("SECTION 2 — Test 1: simulate_step_with_hvac()  (single 5-min step)")
    p()
    p("  One step is tested per scenario at the most relevant hour for that season.")
    p("  Physics is the ground truth — it solves the RC equation deterministically.")
    p("  'Correct' = the engine made the right heat/cool/off decision based on the deadband.")
    p()

    ai_ok = sum(1 for r in step_r if r["ai_correct"])
    ph_ok = sum(1 for r in step_r if r["ph_correct"])

    # Summary table
    p(f"  {'Scenario':<24} {'Gap':>5} {'DB':>4}  {'Need':>5}  "
      f"{'AI Mode':>10}  {'Phys Mode':>10}  {'AI✓':>5}  {'Ph✓':>5}  {'Agree':>6}")
    p("  " + "─" * (W - 2))
    for r in step_r:
        p(f"  {r['scenario']:<24} {r['gap_c']:>5.1f} {r['db']:>4.1f}  "
          f"{'ON' if r['should_on'] else 'OFF':>5}  "
          f"{str(r['ai_mode']):>10}  {str(r['ph_mode']):>10}  "
          f"{'✓' if r['ai_correct'] else '✗':>5}  "
          f"{'✓' if r['ph_correct'] else '✗':>5}  "
          f"{'✓' if r['agree'] else '✗':>6}")
    p()
    p(f"  AI  correct: {ai_ok}/{len(step_r)}  ({100*ai_ok/max(len(step_r),1):.0f}%)")
    p(f"  Physics correct: {ph_ok}/{len(step_r)}  ({100*ph_ok/max(len(step_r),1):.0f}%)")
    p()

    # Detailed per-scenario
    for r in step_r:
        tag = f"[{r['ai_engine'].upper()}]"
        p(f"  ◆ {r['scenario']}  {tag}  latency={r['ai_lat']}s")
        p(f"    Conditions : T_in={r['t_in']}°C → setpoint {r['t_set']}°C, "
          f"T_out={r['t_out']}°C  (hour {r['hour'] if 'hour' in r else r.get('step_hour','?'):02}:00)")
        p(f"    Gap={r['gap_c']}°C vs deadband={r['db']}°C → HVAC should "
          f"{'turn ON' if r['should_on'] else 'stay OFF'}")
        cw = 26
        p(f"    {'Metric':<{cw}} {'AI / Gemini':>13}  {'Physics':>13}  Note")
        p(f"    {'':<{cw}} {'─'*13}  {'─'*13}")
        agree_note = "✓ agree" if r["agree"] else f"✗ differ — physics says {r['ph_mode']}"
        p(f"    {'HVAC mode':<{cw}} {str(r['ai_mode']):>13}  {str(r['ph_mode']):>13}  {agree_note}")
        p(f"    {'Temp change (°C)':<{cw}} {r['ai_td']:>+13.3f}  {r['ph_td']:>+13.3f}  "
          f"delta={r['td_diff']:+.3f}")
        p(f"    {'HVAC power (kW)':<{cw}} {r['ai_power']:>13.3f}  {r['ph_power']:>13.3f}")
        p(f"    {'Step cost ($)':<{cw}} {r['ai_cost']:>13.4f}  {r['ph_cost']:>13.4f}")
        p(f"    {'Decision correct?':<{cw}} {vb(r['ai_correct']):>13}  {vb(r['ph_correct']):>13}")
        p(f"    AI reason  : {r['ai_reason'][:78]}")
        p()

    # ── Test 2: Schedule ──────────────────────────────────────────────────────
    hdr("SECTION 3 — Test 2: generate_hvac_schedule()  (full 24-hour plan)")
    p()
    p("  Each schedule is validated against the RC physics energy floor —")
    p("  the minimum kWh the house must consume to offset its heat loss.")
    p("  'Mode agreement %' = fraction of hours where AI and physics chose the same mode.")
    p()

    for r in sched_r:
        tag  = f"[{r['ai_engine'].upper()}]"
        pb   = "✓ PLAUSIBLE" if r["plausible"] else "✗ ENERGY FLOOR FAILED"
        p(f"  ◆ {r['scenario']}  {tag}  latency={r['ai_lat']}s  {pb}")
        p(f"    Start: T_in={r['t_in']}°C → setpoint {r['t_set']}°C")
        cw = 28
        p(f"    {'Metric':<{cw}} {'AI / Gemini':>13}  {'Physics RC':>13}  {'Delta':>10}")
        p(f"    {'':<{cw}} {'─'*13}  {'─'*13}  {'─'*10}")
        p(f"    {'Total energy (kWh)':<{cw}} {r['ai_kwh']:>13.3f}  {r['ph_kwh']:>13.3f}  "
          f"{r['kwh_delta']:>+10.3f}")
        ratio_str = f"{r['ratio']:.3f}x"
        floor     = r["plaus"]["floor"]
        floor_ok  = r["ai_kwh"] >= floor * 0.20
        p(f"    {'AI / Physics ratio':<{cw}} {ratio_str:>13}  {'1.000x = match':>13}  "
          f"{'floor: ' + str(floor) + ' kWh ' + ('✓' if floor_ok else '✗ BELOW')}")
        p(f"    {'Total cost ($)':<{cw}} {r['ai_cost']:>13.4f}  {r['ph_cost']:>13.4f}  "
          f"{r['cost_delta']:>+10.4f}")
        p(f"    {'Comfort score (%)':<{cw}} {r['ai_comfort']:>13.1f}  {r['ph_comfort']:>13.1f}")
        p(f"    {'Run cycles':<{cw}} {r['ai_cycles']:>13}  {r['ph_cycles']:>13}")
        p(f"    {'Pre-cond events':<{cw}} {r['ai_pre']:>13}  {r['ph_pre']:>13}")
        p(f"    {'Mode agreement (%)':<{cw}} {r['agree_pct']:>13.1f}  "
          f"  {bar(r['agree_pct'])}")
        p()
        if r["plaus_issues"]:
            for iss in r["plaus_issues"]:
                p(f"    ⚠  {iss}")
            p()
        p(f"    AI sample reason : {r['ai_sample'][:78]}")
        p(f"    Physics strategy : {r['ph_strategy'][:78]}")
        p()

    # Summary table
    p("  ── Energy plausibility summary ──────────────────────────────────────────")
    p()
    p(f"  {'Scenario':<24} {'AI kWh':>10} {'Phys kWh':>10} {'Floor kWh':>10} {'Ratio':>8} {'Status':>14}")
    p("  " + "─" * (W - 2))
    for r in sched_r:
        p(f"  {r['scenario']:<24} {r['ai_kwh']:>10.2f} {r['ph_kwh']:>10.2f} "
          f"{r['plaus']['floor']:>10.2f} {r['ratio']:>8.3f}x "
          f"{'✓ ok' if r['plausible'] else '✗ IMPOSSIBLE':>14}")
    p()
    plaus_count = sum(1 for r in sched_r if r["plausible"])
    cheaper     = sum(1 for r in sched_r if r["cost_delta"] < -0.05 and r["plausible"])
    p(f"  Physically plausible: {plaus_count}/{len(sched_r)}")
    if cheaper:
        p(f"  Cheaper than physics (and plausible): {cheaper}/{len(sched_r)}")

    # ── Test 3: Setpoint ──────────────────────────────────────────────────────
    hdr("SECTION 4 — Test 3: optimize_setpoint_ai()  (smart thermostat setpoint)")
    p()
    p("  AI chooses a setpoint that minimises electricity cost while keeping")
    p("  T_in within the user's ±2°C comfort band.  Both engines receive the")
    p("  same TOU pricing context (scenario hour, not real clock).")
    p()

    band_viol = sum(1 for r in setp_r if not r["in_band"])

    for r in setp_r:
        tag   = f"[{r['ai_engine'].upper()}]"
        bb    = "✓ In band" if r["in_band"] else "✗ OUTSIDE BAND"
        pagr  = "✓ agree" if r["pre_agree"] else "✗ differ"
        p(f"  ◆ {r['scenario']}  {tag}  latency={r['ai_lat']}s")
        p(f"    T_in={r['t_in']}°C, comfort={r['comfort']}°C ±{r['tol']}°C, "
          f"T_out={r['t_out']}°C  (hour {r['hour']:02d}:00, "
          f"TOU={get_electricity_price(r['hour']):.3f}/kWh)")
        cw = 30
        p(f"    {'Metric':<{cw}} {'AI / Gemini':>13}  {'Physics':>13}  {'Delta':>8}")
        p(f"    {'':<{cw}} {'─'*13}  {'─'*13}  {'─'*8}")
        p(f"    {'Setpoint (°C)':<{cw}} {r['ai_sp']:>13.1f}  {r['ph_sp']:>13.1f}  "
          f"{r['sp_delta']:>+8.2f}  {bb}")
        p(f"    {'Drift from comfort (°C)':<{cw}} {r['ai_drift']:>13.1f}  {r['ph_drift']:>13.1f}")
        p(f"    {'Pre-conditioning?':<{cw}} {str(r['ai_pre']):>13}  {str(r['ph_pre']):>13}  {pagr}")
        p(f"    {'Expected saving (%)':<{cw}} {r['ai_saving']:>13.1f}  {r['ph_saving']:>13.1f}")
        p(f"    {'Comfort impact':<{cw}} {str(r['ai_impact']):>13}")
        p()
        p(f"    AI strategy  : {r['ai_strategy'][:78]}")
        p(f"    Phys strategy: {r['ph_strategy'][:78]}")
        p()

    p(f"  Setpoint band violations: {band_viol}/{len(setp_r)}"
      f"  ({'All within ±2°C ✓' if band_viol == 0 else '✗ violations detected'})")

    # ── Overall summary ───────────────────────────────────────────────────────
    hdr("SECTION 5 — Overall Summary")
    p()

    step_pct = 100 * ai_ok / max(len(step_r), 1)

    p(f"  {'Function':<32} {'AI score':<26} {'Physics score':<20}")
    p("  " + "─" * (W - 2))
    p(f"  {'simulate_step_with_hvac()':<32} "
      f"{ai_ok}/{len(step_r)} correct ({step_pct:.0f}%)      "
      f"{ph_ok}/{len(step_r)} correct ({100*ph_ok/max(len(step_r),1):.0f}%)")
    p(f"  {'generate_hvac_schedule()':<32} "
      f"{plaus_count}/{len(sched_r)} plausible schedules     "
      f"{len(sched_r)}/{len(sched_r)} plausible schedules")
    p(f"  {'optimize_setpoint_ai()':<32} "
      f"{len(setp_r)-band_viol}/{len(setp_r)} within comfort band    "
      f"{len(setp_r)}/{len(setp_r)} within comfort band")
    p()

    total   = len(step_r) + len(sched_r) + len(setp_r)
    gcount  = (sum(1 for r in step_r  if r["ai_engine"] == "genai") +
               sum(1 for r in sched_r if r["ai_engine"] == "genai") +
               sum(1 for r in setp_r  if r["ai_engine"] not in ("physics", "unknown")))
    avg_lat = round(sum(r["ai_lat"] for r in step_r + sched_r + setp_r) / max(total, 1), 2)
    avg_step_lat  = round(sum(r["ai_lat"] for r in step_r)  / max(len(step_r), 1), 2)
    avg_sched_lat = round(sum(r["ai_lat"] for r in sched_r) / max(len(sched_r), 1), 2)
    avg_setp_lat  = round(sum(r["ai_lat"] for r in setp_r)  / max(len(setp_r), 1), 2)

    p(f"  Total API calls made    : {total}")
    p(f"  Gemini answered         : {gcount} / {total}")
    p(f"  Avg latency — all calls : {avg_lat} s")
    p(f"  Avg latency — step      : {avg_step_lat} s  (physics = <0.01 s)")
    p(f"  Avg latency — schedule  : {avg_sched_lat} s  (physics = <0.01 s)")
    p(f"  Avg latency — setpoint  : {avg_setp_lat} s  (physics = <0.01 s)")
    p()

    # Per-function interpretation
    step_note  = ("✓ AI matches physics on on/off decisions" if step_pct >= 80
                  else "⚠ AI disagrees with physics on some on/off decisions — "
                       "check detailed results above")
    sched_note = ("✓ All schedules are physically plausible" if plaus_count == len(sched_r)
                  else f"⚠ {len(sched_r)-plaus_count} schedule(s) failed the energy floor check — "
                       "physics schedule was used instead")
    setp_note  = ("✓ All setpoints within ±2°C comfort band" if band_viol == 0
                  else f"✗ {band_viol} setpoint(s) violated the comfort band")

    p(f"  Step decisions  : {step_note}")
    p(f"  Schedules       : {sched_note}")
    p(f"  Setpoints       : {setp_note}")

    p()
    p("=" * W)
    p(f"  Gemini live: {'YES' if gemini_live else 'NO (set GENAI_KEY)'}   "
      f"Calls: {total}   Gemini answered: {gcount}   Avg latency: {avg_lat}s")
    p("=" * W)

    if save_path:
        save_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n  [saved] {save_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="HVAC Capstone — AI vs Physics Comparison")
    parser.add_argument("--quick",    action="store_true", help="2 scenarios only")
    parser.add_argument("--scenario", type=str, default=None,
                        help=f"Run one scenario. Options: {list(SCENARIOS.keys())}")
    parser.add_argument("--no-save",  action="store_true", help="Print only, no file saved")
    parser.add_argument("--api-key",  type=str, default=None,
                        help="Google Gemini API key (overrides GENAI_KEY env var)")
    args = parser.parse_args()

    gemini_live = bool(_GENAI_AVAILABLE and GENAI_KEY)

    print()
    print("=" * 60)
    print("  HVAC Capstone — AI vs Physics Comparison")
    print(f"  Gemini: {'✓ LIVE' if gemini_live else '✗ NOT CONFIGURED (set GENAI_KEY)'}")
    print("=" * 60)

    if args.quick:
        run_names = ["cold_winter_day", "hot_summer_day"]
    elif args.scenario:
        if args.scenario not in SCENARIOS:
            print(f"Unknown scenario. Valid options: {list(SCENARIOS.keys())}")
            sys.exit(1)
        run_names = [args.scenario]
    else:
        run_names = list(SCENARIOS.keys())

    step_r, sched_r, setp_r = [], [], []

    for name in run_names:
        sc = {**SCENARIOS[name], "name": name}
        print(f"\n  → {name}  ({sc['desc']})")

        print("     [1/3] simulate_step_with_hvac ...", end="", flush=True)
        r1 = test_step(sc, HOUSE)
        # Store hour for the report
        r1["hour"] = sc["step_hour"]
        step_r.append(r1)
        print(f" engine={r1['ai_engine']}  correct={'✓' if r1['ai_correct'] else '✗'}  "
              f"mode={r1['ai_mode']}  Δtemp={r1['td_diff']:+.3f}°C  lat={r1['ai_lat']}s")

        print("     [2/3] generate_hvac_schedule   ...", end="", flush=True)
        r2 = test_schedule(sc, HOUSE)
        sched_r.append(r2)
        print(f" engine={r2['ai_engine']}  {r2['ai_kwh']:.1f}kWh vs phys {r2['ph_kwh']:.1f}kWh  "
              f"plausible={'✓' if r2['plausible'] else '✗'}  lat={r2['ai_lat']}s")

        print("     [3/3] optimize_setpoint_ai     ...", end="", flush=True)
        r3 = test_setpoint(sc, HOUSE)
        setp_r.append(r3)
        print(f" engine={r3['ai_engine']}  setpoint={r3['ai_sp']}°C  "
              f"band={'✓' if r3['in_band'] else '✗'}  lat={r3['ai_lat']}s")

    out_dir   = OUT_DIR
    save_path = None if (args.quick or args.no_save) else out_dir / "ai_results_report.txt"
    print()
    print_report(step_r, sched_r, setp_r, gemini_live=gemini_live, save_path=save_path)


if __name__ == "__main__":
    main()