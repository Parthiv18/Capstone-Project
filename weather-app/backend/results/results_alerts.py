"""
=============================================================================
  HVAC Capstone — Appliance Alert Model Test Script
  ELE 70A / 70B  ·  Toronto Metropolitan University
=============================================================================
Tests the Gemini appliance scheduling model (build_genai_prompt + Gemini call)
across 5 household scenarios and validates every rule the model must obey.

Usage (run from the weather-app/ directory):
    export GENAI_KEY="your-google-gemini-api-key"
    python track_results_alerts.py              # full run, saves report
    python track_results_alerts.py --quick      # 2 scenarios only
    python track_results_alerts.py --api-key YOUR_KEY
    python track_results_alerts.py --no-save    # print only

Output:
    alerts_results_report.txt  — full human-readable report

What is being validated per scenario
─────────────────────────────────────
  V1  Valid JSON structure       → response has appliance_schedules + alerts keys
  V2  All appliances covered     → every input appliance appears in the schedule
  V3  No past times              → every start_time >= scenario start hour
  V4  Heat-generator rule        → dryer/oven/dishwasher NOT during cooling windows
  V5  High-power stagger         → no two appliances >2 kW start at the same time
  V6  Cost plausibility          → estimated_cost ≈ power_kw × (duration/60) × $0.15 (±50%)
  V7  Priority field valid       → each entry has priority in {high, medium, low}
  V8  EV charger off-peak        → EV charger scheduled in off-peak window if available
  V9  Duration reasonable        → duration_minutes within 50–200% of known run time
  V10 Alert messages present     → at least one alert in the alerts list
=============================================================================
"""

import sys
import os
import json
import re
import math
import time
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

# ── Path setup ────────────────────────────────────────────────────────────────
OUT_DIR      = Path(__file__).resolve().parent
BACKEND_ROOT = OUT_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
_backend_sub = OUT_DIR / "backend"
if _backend_sub.exists() and str(_backend_sub) not in sys.path:
    sys.path.insert(0, str(_backend_sub))

# ── Pre-parse --api-key before importing anything that reads env ──────────────
_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--api-key", default=None)
_pre_args, _ = _pre.parse_known_args()
if _pre_args.api_key:
    os.environ["GENAI_KEY"] = _pre_args.api_key

from api.alerts_simulation.alerts import (
    build_genai_prompt,
    APPLIANCE_POWER_KW,
    APPLIANCE_RUN_TIMES,
    APPLIANCE_HEAT_GENERATORS,
)

GENAI_KEY = os.getenv("GENAI_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
try:
    import google.generativeai as genai
    _GENAI_AVAILABLE = True
    if GENAI_KEY:
        genai.configure(api_key=GENAI_KEY)
except ImportError:
    _GENAI_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
#  ONTARIO TOU (matches hvac_physics.py)
# ─────────────────────────────────────────────────────────────────────────────
def tou_rate(hour: int) -> Tuple[float, str]:
    h = hour % 24
    if h in range(7, 11) or h in range(17, 19):
        return 0.182, "on-peak"
    if h in range(11, 17) or h in range(19, 22):
        return 0.122, "mid-peak"
    return 0.087, "off-peak"


def _is_off_peak(hour: int) -> bool:
    return tou_rate(hour)[0] == 0.087


# ─────────────────────────────────────────────────────────────────────────────
#  SHARED TEST HOUSE
# ─────────────────────────────────────────────────────────────────────────────
HOUSE = {
    "home_size":          1500,
    "insulation_quality": "average",
    "hvac_type":          "central",
    "personal_comfort":   22,
}

ALL_APPLIANCES = list(APPLIANCE_POWER_KW.keys())

HEAT_GENERATORS = {k for k, v in APPLIANCE_HEAT_GENERATORS.items() if v}
HIGH_POWER_APPS = {k for k, v in APPLIANCE_POWER_KW.items() if v >= 2.0}


# ─────────────────────────────────────────────────────────────────────────────
#  SYNTHETIC HVAC SCHEDULES (representative 24h plans)
# ─────────────────────────────────────────────────────────────────────────────
def _make_hvac_schedule(
    mode: str,        # "heating" | "cooling" | "off"
    active_hours: List[int],
    power_kw: float,
) -> Dict:
    """Build a minimal hvac_sim dict matching the format alerts.py expects."""
    actions = []
    for h in range(24):
        m = mode if h in active_hours else "off"
        actions.append({
            "hour":       h,
            "mode":       m,
            "start_time": f"{h:02d}:00",
            "end_time":   f"{(h+1)%24:02d}:00",
            "power_kw":   power_kw if m != "off" else 0.0,
            "cost":       round(power_kw * tou_rate(h)[0], 4) if m != "off" else 0.0,
        })
    total_kwh = power_kw * len(active_hours)
    return {
        "actions":          actions,
        "total_energy_kwh": round(total_kwh, 2),
        "total_cost":       round(sum(power_kw * tou_rate(h)[0] for h in active_hours), 3),
        "comfort_score":    100.0,
    }


def _make_weather(t_day: float, t_night: float, humidity: float, n: int = 24) -> List[Dict]:
    rows = []
    for h in range(n):
        phase = math.pi * (h - 5) / 9
        t = t_night + (t_day - t_night) * max(0.0, math.sin(phase))
        rows.append({
            "temperature_2m":       round(t, 1),
            "relative_humidity_2m": humidity,
        })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
#  TEST SCENARIOS
# ─────────────────────────────────────────────────────────────────────────────
# step_hour = the simulated "current" hour the prompt will be built with.
# We patch datetime.now() by overriding build_genai_prompt's clock-sensitive
# parts via a thin wrapper that temporarily monkeypatches the module's datetime.

SCENARIOS: Dict[str, Dict[str, Any]] = {
    "winter_morning_peak": {
        "desc":        "Winter weekday 08:00 — on-peak, HVAC heating, full appliance set",
        "step_hour":   8,
        "t_in":        20.0,
        "t_set":       22.0,
        "appliances":  ALL_APPLIANCES,
        "hvac":        _make_hvac_schedule("heating", list(range(5, 10)) + list(range(17, 21)), 9.29),
        "weather":     _make_weather(-10, -18, 65),
        "season":      "winter",
        # V4 check: no heat-generators should run while HVAC is cooling (N/A — heating)
        "hvac_cooling_hours": [],
    },
    "summer_afternoon_peak": {
        "desc":        "Summer afternoon 14:00 — mid-peak, HVAC active cooling, heat-generators banned",
        "step_hour":   14,
        "t_in":        26.0,
        "t_set":       22.0,
        "appliances":  [
            "Clothes Dryer (Electric or Gas)",
            "Dishwasher (especially drying cycles)",
            "Oven (Electric or Gas)",
            "Washing Machine (hot water cycles)",
            "Electric Vehicle Charger (Level 1 or Level 2)",
        ],
        "hvac":        _make_hvac_schedule("cooling", list(range(10, 20)), 4.47),
        "weather":     _make_weather(33, 22, 78),
        "season":      "summer",
        "hvac_cooling_hours": list(range(10, 20)),
    },
    "offpeak_night": {
        "desc":        "Late evening 22:00 — off-peak, HVAC off, best window for heavy loads",
        "step_hour":   22,
        "t_in":        22.0,
        "t_set":       22.0,
        "appliances":  [
            "Electric Vehicle Charger (Level 1 or Level 2)",
            "Clothes Dryer (Electric or Gas)",
            "Dishwasher (especially drying cycles)",
            "Washing Machine (hot water cycles)",
            "Electric Water Heater",
        ],
        "hvac":        _make_hvac_schedule("off", [], 0.0),
        "weather":     _make_weather(15, 8, 50),
        "season":      "shoulder",
        "hvac_cooling_hours": [],
    },
    "shoulder_midday": {
        "desc":        "Shoulder season 11:00 — mid-peak, minimal HVAC, mixed appliances",
        "step_hour":   11,
        "t_in":        21.0,
        "t_set":       22.0,
        "appliances":  [
            "Oven (Electric or Gas)",
            "Stove / Cooktop (Electric, Gas, or Induction)",
            "Washing Machine (hot water cycles)",
            "Dishwasher (especially drying cycles)",
            "Gas Water Heater",
        ],
        "hvac":        _make_hvac_schedule("heating", [6, 7], 9.29),
        "weather":     _make_weather(14, 6, 40),
        "season":      "shoulder",
        "hvac_cooling_hours": [],
    },
    "full_house_evening": {
        "desc":        "Full house load 17:00 — on-peak, heating starting, all high-power appliances",
        "step_hour":   17,
        "t_in":        20.5,
        "t_set":       22.0,
        "appliances":  [
            "Clothes Dryer (Electric or Gas)",
            "Dishwasher (especially drying cycles)",
            "Electric Water Heater",
            "Oven (Electric or Gas)",
            "Electric Vehicle Charger (Level 1 or Level 2)",
            "Washing Machine (hot water cycles)",
        ],
        "hvac":        _make_hvac_schedule("heating", list(range(17, 22)), 9.29),
        "weather":     _make_weather(-5, -12, 60),
        "season":      "winter",
        "hvac_cooling_hours": [],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
#  GEMINI CALLER  (direct, no DB dependency)
# ─────────────────────────────────────────────────────────────────────────────
def _call_gemini(prompt: str) -> Tuple[Optional[Dict], float, str]:
    """
    Call Gemini directly with the prompt.
    Returns (parsed_result, latency_s, error_msg).
    """
    if not (_GENAI_AVAILABLE and GENAI_KEY):
        return None, 0.0, "GENAI_KEY not set"

    t0 = time.perf_counter()
    try:
        model    = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.2,
                max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )
        latency = round(time.perf_counter() - t0, 2)
        text    = response.text.strip()

        # Strip markdown fences
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$",           "", text)
        text = text.strip()

        # Three parse attempts
        result = None
        for attempt in (
            text,
            text[text.find("{"):text.rfind("}")+1],
            re.sub(r",\s*([}\]])", r"\1", text),
        ):
            try:
                result = json.loads(attempt)
                break
            except (json.JSONDecodeError, ValueError):
                pass

        if result is None:
            return None, latency, "JSON parse failed"
        return result, latency, ""

    except Exception as exc:
        latency = round(time.perf_counter() - t0, 2)
        return None, latency, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
#  VALIDATION CHECKS
# ─────────────────────────────────────────────────────────────────────────────
def _parse_hhmm(t: str) -> int:
    """HH:MM → minutes from midnight. Returns -1 on failure."""
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return -1


def validate(result: Dict, scenario: Dict) -> List[Dict]:
    """
    Run all 10 validation checks.
    Returns a list of {id, name, passed, detail} dicts.
    """
    checks = []
    schedules = result.get("appliance_schedules", [])
    alerts    = result.get("alerts", [])
    hour      = scenario["step_hour"]
    start_min = hour * 60
    appliances_in = scenario["appliances"]
    cooling_hours = set(scenario["hvac_cooling_hours"])

    def chk(vid, name, passed, detail=""):
        checks.append({"id": vid, "name": name, "passed": passed, "detail": detail})

    # V1 — valid structure
    has_sched = isinstance(schedules, list)
    has_alert = isinstance(alerts, list)
    chk("V1", "Valid JSON structure",
        has_sched and has_alert,
        f"appliance_schedules={'list' if has_sched else type(schedules).__name__}, "
        f"alerts={'list' if has_alert else type(alerts).__name__}")

    # V2 — all appliances covered
    scheduled_names = {s.get("appliance","").strip() for s in schedules}
    missing = [a for a in appliances_in if a not in scheduled_names]
    chk("V2", "All appliances covered",
        len(missing) == 0,
        f"Missing: {missing}" if missing else f"All {len(appliances_in)} covered")

    # V3 — no past times
    past = []
    for s in schedules:
        t = s.get("optimal_start_time","")
        mins = _parse_hhmm(t)
        if mins != -1 and mins < start_min:
            past.append(f"{s.get('appliance','')} @ {t}")
    chk("V3", "No past start times",
        len(past) == 0,
        f"Past times: {past}" if past else f"All start times >= {hour:02d}:00")

    # V4 — heat-generators not during cooling
    violations = []
    if cooling_hours:
        for s in schedules:
            app = s.get("appliance","")
            if app in HEAT_GENERATORS:
                t = s.get("optimal_start_time","")
                h_start = _parse_hhmm(t) // 60 if _parse_hhmm(t) != -1 else -1
                if h_start in cooling_hours:
                    violations.append(f"{app} @ {t}")
    chk("V4", "Heat-generators avoid cooling windows",
        len(violations) == 0,
        f"Violations: {violations}" if violations
        else ("No cooling windows" if not cooling_hours else "Rule respected"))

    # V5 — high-power stagger (no two ≥2 kW at same start time)
    from collections import defaultdict
    slot_map = defaultdict(list)
    for s in schedules:
        app = s.get("appliance","")
        pwr = APPLIANCE_POWER_KW.get(app, s.get("power_kw", 0))
        if pwr >= 2.0:
            slot_map[s.get("optimal_start_time","?")].append(app)
    conflicts = {t: apps for t, apps in slot_map.items() if len(apps) > 1}
    chk("V5", "High-power appliances staggered",
        len(conflicts) == 0,
        f"Conflicts: {conflicts}" if conflicts else "No stacking detected")

    # V6 — cost plausibility (within ±50% of power × duration/60 × $0.15)
    bad_cost = []
    for s in schedules:
        app  = s.get("appliance","")
        pwr  = APPLIANCE_POWER_KW.get(app, s.get("power_kw", 1.0))
        dur  = s.get("duration_minutes", APPLIANCE_RUN_TIMES.get(app, 60))
        est  = s.get("estimated_cost", None)
        if est is None or dur <= 0:
            continue
        expected = pwr * (dur / 60) * 0.15
        if expected > 0 and not (0.5 * expected <= est <= 2.0 * expected):
            bad_cost.append(f"{app}: got ${est:.3f}, expected ~${expected:.3f}")
    chk("V6", "Cost calculations plausible (±50%)",
        len(bad_cost) == 0,
        f"Issues: {bad_cost[:3]}" if bad_cost else "All costs within range")

    # V7 — priority field valid
    valid_priorities = {"high", "medium", "low"}
    bad_pri = [s.get("appliance","?") for s in schedules
               if s.get("priority","").lower() not in valid_priorities]
    chk("V7", "Priority field is valid",
        len(bad_pri) == 0,
        f"Bad priority on: {bad_pri}" if bad_pri else "All priorities valid")

    # V8 — EV charger prefers off-peak (if EV is in the scenario)
    ev_name = "Electric Vehicle Charger (Level 1 or Level 2)"
    if ev_name in appliances_in:
        ev_entry = next((s for s in schedules if s.get("appliance","") == ev_name), None)
        if ev_entry:
            ev_hour = _parse_hhmm(ev_entry.get("optimal_start_time","")) // 60
            off_pk  = _is_off_peak(ev_hour) if ev_hour >= 0 else False
            chk("V8", "EV charger scheduled off-peak",
                off_pk,
                f"EV at {ev_entry.get('optimal_start_time','')} "
                f"({'off-peak ✓' if off_pk else 'NOT off-peak ✗'})")
        else:
            chk("V8", "EV charger scheduled off-peak", False, "EV not found in schedule")
    else:
        chk("V8", "EV charger scheduled off-peak", True, "N/A — no EV in scenario")

    # V9 — duration reasonable (50%–200% of known run time)
    bad_dur = []
    for s in schedules:
        app      = s.get("appliance","")
        known    = APPLIANCE_RUN_TIMES.get(app)
        reported = s.get("duration_minutes")
        if known and reported:
            if not (0.5 * known <= reported <= 2.0 * known):
                bad_dur.append(f"{app}: {reported} min (expected ~{known} min)")
    chk("V9", "Durations within 50–200% of expected",
        len(bad_dur) == 0,
        f"Issues: {bad_dur[:3]}" if bad_dur else "All durations reasonable")

    # V10 — at least one alert present
    chk("V10", "Alert messages present",
        len(alerts) > 0,
        f"{len(alerts)} alerts" if alerts else "No alerts returned")

    return checks


# ─────────────────────────────────────────────────────────────────────────────
#  SCENARIO RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def run_scenario(name: str, scenario: Dict) -> Dict:
    """Build prompt, call Gemini, validate, return full result dict."""
    # Patch datetime in the alerts module so the prompt gets the right hour
    import api.alerts_simulation.alerts as _am
    from unittest.mock import patch, MagicMock

    fake_now = MagicMock()
    fake_now.hour   = scenario["step_hour"]
    fake_now.minute = 0
    fake_dt = MagicMock()
    fake_dt.now.return_value = fake_now

    with patch.object(_am, "datetime", fake_dt):
        prompt = build_genai_prompt(
            appliances    = scenario["appliances"],
            hvac_schedule = scenario["hvac"],
            weather_data  = scenario["weather"],
            house_data    = HOUSE,
            current_temp_c = scenario["t_in"],
            target_temp_c  = scenario["t_set"],
        )

    result, latency, error = _call_gemini(prompt)

    if result is None:
        return {
            "name":     name,
            "scenario": scenario,
            "result":   None,
            "latency":  latency,
            "error":    error,
            "checks":   [],
            "passed":   0,
            "total":    10,
            "engine":   "failed",
        }

    checks = validate(result, scenario)
    passed = sum(1 for c in checks if c["passed"])

    return {
        "name":       name,
        "scenario":   scenario,
        "result":     result,
        "latency":    latency,
        "error":      "",
        "checks":     checks,
        "passed":     passed,
        "total":      len(checks),
        "engine":     "genai",
        "num_schedules": len(result.get("appliance_schedules", [])),
        "num_alerts":    len(result.get("alerts", [])),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  REPORT
# ─────────────────────────────────────────────────────────────────────────────
CHECK_NAMES = {
    "V1":  "Valid JSON structure",
    "V2":  "All appliances covered",
    "V3":  "No past start times",
    "V4":  "Heat-generators avoid cooling",
    "V5":  "High-power loads staggered",
    "V6":  "Cost calculations plausible",
    "V7":  "Priority field valid",
    "V8":  "EV charger off-peak",
    "V9":  "Durations reasonable",
    "V10": "Alert messages present",
}


def print_report(results: List[Dict], gemini_live: bool, save_path: Optional[Path] = None):
    lines = []
    W = 84

    def p(s=""):
        lines.append(s)
        print(s)

    def hdr(t):
        p(); p("─" * W); p(f"  {t}"); p("─" * W)

    p("=" * W)
    p("  HVAC Capstone — Appliance Alert Model Test Report")
    p(f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    p(f"  Gemini    : {'✓ LIVE — real Gemini responses' if gemini_live else '✗ NOT CONFIGURED (set GENAI_KEY)'}")
    p("=" * W)

    # ── What is being tested ──────────────────────────────────────────────────
    hdr("SECTION 1 — What is being tested")
    p()
    p("  The appliance scheduling model (build_genai_prompt → Gemini) is called")
    p("  directly, bypassing the database layer. Each scenario provides a realistic")
    p("  set of appliances, an HVAC schedule, and weather forecast. 10 rule-based")
    p("  validation checks are run on every response.")
    p()
    p("  Scenarios cover: winter on-peak heating, summer cooling with heat-generator")
    p("  ban, off-peak night with EV charger, shoulder-season midday, and a full")
    p("  evening house load during on-peak heating.")
    p()
    p("  Validation checks:")
    for vid, name in CHECK_NAMES.items():
        p(f"    {vid:<4}  {name}")

    # ── Per-scenario results ──────────────────────────────────────────────────
    hdr("SECTION 2 — Scenario Results")
    p()

    for r in results:
        sc   = r["scenario"]
        tag  = f"[{r['engine'].upper()}]"
        pct  = f"{r['passed']}/{r['total']}"
        star = "✓ PASS" if r["passed"] == r["total"] else f"✗ {r['total']-r['passed']} FAIL"

        p(f"  ◆ {r['name']}  {tag}  {pct} checks  {star}  latency={r['latency']}s")
        p(f"    {sc['desc']}")
        p(f"    Hour={sc['step_hour']:02d}:00  TOU={tou_rate(sc['step_hour'])[0]:.3f}/kWh ({tou_rate(sc['step_hour'])[1]})")
        p(f"    T_in={sc['t_in']}°C → setpoint {sc['t_set']}°C")
        p(f"    Appliances tested: {len(sc['appliances'])}")
        p(f"    HVAC cooling hours: {sc['hvac_cooling_hours'] if sc['hvac_cooling_hours'] else 'none (no active cooling)'}")
        p()

        if r["result"] is None:
            p(f"    ✗ NO RESULT — Gemini call failed: {r['error']}")
            p()
            continue

        p(f"    Schedules returned: {r['num_schedules']}  |  Alerts returned: {r['num_alerts']}")
        p()

        # Check table
        p(f"    {'Check':<6} {'Name':<34} {'Result':>8}  Detail")
        p("    " + "─" * (W - 4))
        for c in r["checks"]:
            icon   = "✓" if c["passed"] else "✗"
            detail = c["detail"][:45] if c["detail"] else ""
            p(f"    {c['id']:<6} {c['name']:<34} {icon:>8}  {detail}")
        p()

        # Schedule preview (first 3 appliances)
        schedules = r["result"].get("appliance_schedules", [])
        if schedules:
            p("    Schedule preview (first 3 entries):")
            p(f"    {'Appliance':<42} {'Start':>7} {'End':>7} {'Dur':>5} {'Cost':>8}  Priority")
            p("    " + "─" * (W - 4))
            for s in schedules[:3]:
                app  = s.get("appliance","")[:40]
                st   = s.get("optimal_start_time","?")
                en   = s.get("optimal_end_time","?")
                dur  = s.get("duration_minutes","?")
                cost = f"${s.get('estimated_cost',0):.3f}"
                pri  = s.get("priority","?")
                p(f"    {app:<42} {st:>7} {en:>7} {dur:>5} {cost:>8}  {pri}")
            if len(schedules) > 3:
                p(f"    ... and {len(schedules)-3} more")
        p()

    # ── Check-by-check summary across all scenarios ───────────────────────────
    hdr("SECTION 3 — Check-by-Check Summary")
    p()
    header_row = f"  {'Check':<6} {'Name':<34}" + "".join(f" {r['name'][:12]:>13}" for r in results)
    p(header_row)
    p("  " + "─" * (W - 2))

    all_check_ids = list(CHECK_NAMES.keys())
    for vid in all_check_ids:
        row = f"  {vid:<6} {CHECK_NAMES[vid]:<34}"
        all_pass = True
        for r in results:
            if r["result"] is None:
                row += f" {'N/A':>13}"
                continue
            c = next((x for x in r["checks"] if x["id"] == vid), None)
            sym = ("✓" if c["passed"] else "✗") if c else "?"
            if c and not c["passed"]:
                all_pass = False
            row += f" {sym:>13}"
        p(row)

    p()
    p("  Legend: ✓ = pass   ✗ = fail   N/A = Gemini unavailable")

    # ── Overall summary ───────────────────────────────────────────────────────
    hdr("SECTION 4 — Overall Summary")
    p()

    completed = [r for r in results if r["result"] is not None]
    all_pass  = [r for r in completed if r["passed"] == r["total"]]
    total_checks = sum(r["total"] for r in completed)
    total_passed = sum(r["passed"] for r in completed)
    avg_lat      = round(sum(r["latency"] for r in results) / max(len(results), 1), 2)

    p(f"  Scenarios run          : {len(results)}")
    p(f"  Gemini answered        : {len(completed)} / {len(results)}")
    p(f"  Scenarios all-pass     : {len(all_pass)} / {len(completed)}")
    p(f"  Total checks passed    : {total_passed} / {total_checks}")
    p(f"  Avg latency            : {avg_lat} s")
    p()

    # Check-level pass rates
    p(f"  {'Check':<6} {'Name':<34} {'Pass rate':>12}")
    p("  " + "─" * (W - 2))
    for vid in all_check_ids:
        relevant = [r for r in completed
                    if any(c["id"] == vid for c in r["checks"])]
        n_pass   = sum(1 for r in relevant
                       if any(c["id"] == vid and c["passed"] for c in r["checks"]))
        rate_str = f"{n_pass}/{len(relevant)}" if relevant else "N/A"
        bar_len  = int(20 * n_pass / max(len(relevant), 1)) if relevant else 0
        bar      = "█" * bar_len + "░" * (20 - bar_len)
        p(f"  {vid:<6} {CHECK_NAMES[vid]:<34} {rate_str:>6}  {bar}")

    p()
    if total_checks > 0:
        overall_pct = 100 * total_passed / total_checks
        p(f"  Overall pass rate: {total_passed}/{total_checks} ({overall_pct:.0f}%)")
    p()
    p("=" * W)
    p(f"  Gemini live: {'YES' if gemini_live else 'NO'}   Scenarios: {len(results)}   "
      f"Checks passed: {total_passed}/{total_checks}   Avg latency: {avg_lat}s")
    p("=" * W)

    if save_path:
        save_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n  [saved] {save_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="HVAC Capstone — Appliance Alert Model Test")
    parser.add_argument("--quick",    action="store_true", help="Run 2 scenarios only")
    parser.add_argument("--scenario", type=str, default=None,
                        help=f"One scenario: {list(SCENARIOS.keys())}")
    parser.add_argument("--no-save",  action="store_true", help="Print only")
    parser.add_argument("--api-key",  type=str, default=None)
    args = parser.parse_args()

    gemini_live = bool(_GENAI_AVAILABLE and GENAI_KEY)

    print()
    print("=" * 60)
    print("  HVAC Capstone — Appliance Alert Model Test")
    print(f"  Gemini: {'✓ LIVE' if gemini_live else '✗ NOT CONFIGURED (set GENAI_KEY)'}")
    print("=" * 60)

    if args.quick:
        run_names = ["summer_afternoon_peak", "offpeak_night"]
    elif args.scenario:
        if args.scenario not in SCENARIOS:
            print(f"Unknown scenario. Options: {list(SCENARIOS.keys())}")
            sys.exit(1)
        run_names = [args.scenario]
    else:
        run_names = list(SCENARIOS.keys())

    results = []
    for name in run_names:
        sc = SCENARIOS[name]
        print(f"\n  → {name}")
        print(f"     {sc['desc']}")
        print(f"     Calling Gemini with {len(sc['appliances'])} appliances ...", end="", flush=True)
        r = run_scenario(name, sc)
        results.append(r)
        if r["result"] is None:
            print(f" ✗ FAILED — {r['error']}")
        else:
            print(f" {r['passed']}/{r['total']} checks  lat={r['latency']}s  "
                  f"schedules={r['num_schedules']}  alerts={r['num_alerts']}")

    out_dir   = OUT_DIR
    save_path = None if (args.quick or args.no_save) else out_dir / "alerts_results_report.txt"
    print()
    print_report(results, gemini_live=gemini_live, save_path=save_path)


if __name__ == "__main__":
    main()