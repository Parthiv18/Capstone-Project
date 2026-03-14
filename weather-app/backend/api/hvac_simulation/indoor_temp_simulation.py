"""
Indoor Temperature Simulation Module
=====================================
Orchestrates the two-tier engine (Gemini → RC physics fallback)
and integrates with the database for state management.

Call surface unchanged from original — existing routes work without edits.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

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
    get_target_setpoint,
)
from .hvac_physics import (
    generate_hvac_schedule,
    get_current_hvac_action,
    get_upcoming_actions,
    simulate_step_with_hvac,
    predict_temperature,
    optimize_setpoint_ai,
    celsius_to_fahrenheit,
    HVACSchedule,
    get_electricity_price,
    _SETPOINT_REFRESH_INTERVAL_S,
)

DEFAULT_TIMESTEP_MINUTES = 5
MAX_TIMESTEP_SECONDS     = 300   # cap: never advance more than 5 min in one poll
FIRST_STEP_SECONDS       = 60    # first-call step size — avoids a sudden temp jump on mount
_SCHEDULE_MAX_AGE_S      = 3600  # regenerate schedule after 1 hour

# Per-user wall-clock time of last simulation step.
# Key: user_id (int)  Value: datetime of last successful step
_last_step_times: Dict[int, datetime] = {}


def _schedule_is_stale(hvac_schedule: Optional[Dict]) -> bool:
    """
    True when the stored schedule should be regenerated.

    A schedule is stale when:
      1. It doesn't exist, OR
      2. Its generated_at timestamp is older than _SCHEDULE_MAX_AGE_S (1 hour), OR
      3. Every non-off action's end_time is already in the past.

    Rule 3 lets a normal day's schedule run to completion without being
    blown away by periodic refreshes — only regenerate once everything
    planned has actually happened.
    """
    if hvac_schedule is None:
        return True

    # ── Age check ──────────────────────────────────────────────────────────
    generated_at = hvac_schedule.get("generated_at")
    if generated_at:
        try:
            gen_time = datetime.fromisoformat(generated_at)
            if (datetime.now() - gen_time).total_seconds() > _SCHEDULE_MAX_AGE_S:
                return True
        except Exception:
            pass

    # ── All actions exhausted check ────────────────────────────────────────
    now_mins = datetime.now().hour * 60 + datetime.now().minute
    for action in hvac_schedule.get("actions", []):
        if action.get("mode", "off") == "off":
            continue
        end_str = action.get("end_time", "")
        if not end_str:
            continue
        try:
            h, m = end_str.split(":")
            end_abs = int(h) * 60 + int(m)
            # Minutes until this action ends (wrap across midnight)
            mins_until_end = (end_abs - now_mins) % (24 * 60)
            if mins_until_end < 20 * 60:   # still within next 20 h → not stale
                return False
        except Exception:
            continue
    # Every non-off action is far in the future or all are exhausted
    return True


# ════════════════════════════════════════════════════════════════════════════
#  AI Setpoint State
#  Tracks per-user AI setpoint mode without requiring a DB schema change.
#  All state is in-memory; it resets on process restart (acceptable for a
#  simulation — the AI will simply recompute on the next step).
# ════════════════════════════════════════════════════════════════════════════

from dataclasses import dataclass, field as dc_field

@dataclass
class _AiSetpointRecord:
    enabled:              bool     = False   # True = AI controls the setpoint
    ai_setpoint_c:        float    = 22.0    # last AI-chosen setpoint
    comfort_tol:          float    = 2.0     # comfort band half-width (°C)
    last_computed:        Optional[datetime] = None   # when setpoint was last refreshed
    last_strategy:        str      = ""      # human-readable last decision
    last_source:          str      = "none"  # "genai" | "physics" | "none"
    pre_conditioning:     bool     = False
    # Manual override: user may drag the thermostat even in AI mode.
    # The override holds for `override_duration_s` seconds, then AI resumes.
    manual_override_c:    Optional[float]    = None
    manual_override_until: Optional[datetime] = None

# Key: user_id (int)
_ai_setpoint_state: Dict[int, _AiSetpointRecord] = {}


def _get_ai_record(user_id: int) -> _AiSetpointRecord:
    if user_id not in _ai_setpoint_state:
        _ai_setpoint_state[user_id] = _AiSetpointRecord()
    return _ai_setpoint_state[user_id]


def _manual_override_active(rec: _AiSetpointRecord) -> bool:
    """True if a manual override is in effect right now."""
    if rec.manual_override_c is None or rec.manual_override_until is None:
        return False
    return datetime.now() < rec.manual_override_until


def _setpoint_needs_refresh(rec: _AiSetpointRecord) -> bool:
    """True if the AI setpoint is stale and should be recomputed."""
    if rec.last_computed is None:
        return True
    age_s = (datetime.now() - rec.last_computed).total_seconds()
    return age_s >= _SETPOINT_REFRESH_INTERVAL_S


def _get_or_refresh_ai_setpoint(
    user_id:      int,
    house_data:   Dict,
    t_in:         float,
    weather_rows: List[Dict],
    comfort_c:    float,
) -> float:
    """
    Return the current AI-managed setpoint for user_id.

    Refreshes (calls optimize_setpoint_ai) when:
      • No setpoint has been computed yet, OR
      • The last result is older than _SETPOINT_REFRESH_INTERVAL_S (30 min)

    During a manual override the override value is returned instead.
    """
    rec = _get_ai_record(user_id)

    # Manual override takes precedence
    if _manual_override_active(rec):
        return rec.manual_override_c  # type: ignore[return-value]

    if _setpoint_needs_refresh(rec):
        try:
            result = optimize_setpoint_ai(
                house_data   = house_data,
                t_in         = t_in,
                weather_rows = weather_rows,
                comfort_c    = comfort_c,
                comfort_tol  = rec.comfort_tol,
            )
            rec.ai_setpoint_c    = float(result["optimal_setpoint_c"])
            rec.last_computed    = datetime.now()
            rec.last_strategy    = result.get("strategy", "")
            rec.last_source      = result.get("source", "physics")
            rec.pre_conditioning = bool(result.get("pre_conditioning", False))
        except Exception as exc:
            # Never crash the simulation step — keep the last known setpoint
            print(f"[AI Setpoint] Optimisation failed: {exc}")

    return rec.ai_setpoint_c


# ════════════════════════════════════════════════════════════════════════════
#  Internal helpers
# ════════════════════════════════════════════════════════════════════════════

def _parse_weather_row_time(date_str: str) -> Optional[datetime]:
    """Parse '2025-12-19 14:00:00 EST' — drops timezone token."""
    if not isinstance(date_str, str):
        return None
    parts = date_str.split(" ")
    base  = " ".join(parts[:2]) if len(parts) >= 3 else date_str
    try:
        return datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _select_weather_row(weather_rows: List[Dict], target_time: datetime) -> Optional[Dict]:
    """Return the row whose datetime is closest to target_time."""
    best_row, best_delta = None, None
    for row in weather_rows:
        if not isinstance(row, dict):
            continue
        t = _parse_weather_row_time(row.get("date", ""))
        if t is None:
            continue
        delta = abs((t - target_time).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta, best_row = delta, row
    return best_row


def _extract_weather_rows(raw_weather: Any) -> List[Dict]:
    """Normalise weather data from any of the formats the backend may store."""
    if isinstance(raw_weather, dict):
        return raw_weather.get("rows", [])
    if isinstance(raw_weather, list):
        if raw_weather and isinstance(raw_weather[0], dict):
            if "data" in raw_weather[0]:
                # Snapshot list — use latest
                data = raw_weather[-1].get("data", {})
                return data.get("rows", []) if isinstance(data, dict) else []
            return raw_weather
    return []


def _resolve_target_temp(
    user_id:         int,
    state:           Dict,
    house_data:      Dict,
    explicit_target: Optional[float] = None,
    weather_rows:    Optional[List[Dict]] = None,
    t_in:            Optional[float] = None,
) -> float:
    """
    Priority chain for setpoint resolution — now AI-aware.

      1. Explicit value passed by caller (e.g. manual thermostat drag).
         • In AI mode: stores as a timed manual override, AI resumes after expiry.
         • Outside AI mode: behaves exactly as before (saved to DB).
      2. AI-managed setpoint (when AI mode is enabled and no active override).
         Calls optimize_setpoint_ai() which tries Gemini then falls back to
         physics cost-minimisation.  Refreshes every 30 minutes.
      3. Saved setpoint in DB (AI mode disabled / AI not yet computed).
      4. personal_comfort from house form.
      5. Hard default: 22 °C.

    All values clamped to [15, 30] °C.
    """
    def clamp(v: float) -> float:
        return max(15.0, min(30.0, float(v)))

    # ── Derive comfort centre (needed for AI mode) ──────────────────────────
    raw_comfort = house_data.get("personal_comfort")
    comfort_c   = clamp(float(raw_comfort)) if raw_comfort is not None else 22.0

    rec = _get_ai_record(user_id)

    # ── 1. Explicit caller value ─────────────────────────────────────────────
    if explicit_target is not None:
        new_t = clamp(explicit_target)
        if rec.enabled:
            # Treat as a 60-minute manual override; AI resumes afterwards
            from datetime import timedelta
            rec.manual_override_c     = new_t
            rec.manual_override_until = datetime.now() + timedelta(minutes=60)
        else:
            set_target_setpoint(user_id, new_t)
        return new_t

    # ── 2. AI-managed setpoint ───────────────────────────────────────────────
    if rec.enabled:
        rows = weather_rows or []
        cur_t = float(t_in) if t_in is not None else comfort_c
        ai_sp = _get_or_refresh_ai_setpoint(
            user_id      = user_id,
            house_data   = house_data,
            t_in         = cur_t,
            weather_rows = rows,
            comfort_c    = comfort_c,
        )
        # Persist so DB / frontend stays in sync
        set_target_setpoint(user_id, ai_sp)
        return ai_sp

    # ── 3–5. Original chain (manual mode) ───────────────────────────────────
    saved = state.get("target_setpoint")
    if saved is not None:
        return clamp(saved)

    if raw_comfort is not None:
        set_target_setpoint(user_id, comfort_c)
        return comfort_c

    set_target_setpoint(user_id, 22.0)
    return 22.0


def _parse_hhmm_mins(t: str) -> int:
    """HH:MM → minutes from midnight."""
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _relative_time_label(start_time: str) -> str:
    """Return human-friendly label like 'In 47 min', 'In 2 h 14 min', 'Now'."""
    now_mins  = datetime.now().hour * 60 + datetime.now().minute
    start_abs = _parse_hhmm_mins(start_time)
    diff      = (start_abs - now_mins) % (24 * 60)    # minutes until start
    if diff <= 2:
        return "Now"
    if diff < 60:
        return f"In {diff} min"
    h, m = divmod(diff, 60)
    return f"In {h} h {m} min" if m else f"In {h} h"


def _is_action_active(action: Dict) -> bool:
    """True if this action's time window contains the current wall-clock minute."""
    now_mins = datetime.now().hour * 60 + datetime.now().minute
    start    = _parse_hhmm_mins(action.get("start_time", "00:00"))
    end      = _parse_hhmm_mins(action.get("end_time",   "00:00"))
    if end == 0:          # no end_time set
        return False
    if start <= end:
        return start <= now_mins < end
    else:                 # midnight wrap  e.g. 23:30 – 00:30
        return now_mins >= start or now_mins < end


def _mins_remaining(action: Dict) -> int:
    """Minutes until this action's end_time (negative when already past)."""
    now_mins = datetime.now().hour * 60 + datetime.now().minute
    end      = _parse_hhmm_mins(action.get("end_time", "00:00"))
    remaining = (end - now_mins) % (24 * 60)
    # Treat a full-day wrap as 0 (expired)
    return remaining if remaining < 23 * 60 else 0


def _format_notifications(
    schedule_dict: Dict,
    current_hour:  int,
    limit:         int = 5,
) -> List[Dict]:
    """
    Return up to `limit` schedule entries:
      • Slot 0 is ALWAYS the currently-active window (if any HVAC is running now).
        It shows "Now  18:25 – 20:30  (9.3 kW, $2.73, 45 min left)"
      • Remaining slots show upcoming non-off windows in chronological order.

    This fixes the "heating now but schedule says In 18 min" bug: the active
    period is no longer excluded just because its start_time is in the past.
    """
    now_mins  = datetime.now().hour * 60 + datetime.now().minute
    all_nonoff = [
        a for a in schedule_dict.get("actions", [])
        if a.get("mode", "off") != "off"
    ]

    def mins_until_start(action: Dict) -> int:
        start = _parse_hhmm_mins(action.get("start_time", "00:00"))
        return (start - now_mins) % (24 * 60)

    # Split into active (running right now) and upcoming
    active_actions   = [a for a in all_nonoff if _is_action_active(a)]
    upcoming_actions = sorted(
        [a for a in all_nonoff if not _is_action_active(a)
         and mins_until_start(a) <= 12 * 60],   # within next 12 hours
        key=mins_until_start,
    )

    notifications: List[Dict] = []

    def _build_entry(action: Dict, is_active: bool) -> Dict:
        start_str = action.get("start_time", "")
        end_str   = action.get("end_time",   "")
        mode_str  = action.get("mode", "").upper()
        power     = action.get("power_kw", 0)
        cost      = action.get("cost", 0)

        if is_active:
            mins_left = _mins_remaining(action)
            time_label = "Now"
            mu         = 0
            dur_str    = f", {mins_left} min left" if mins_left > 0 else ""
        else:
            mu         = mins_until_start(action)
            time_label = _relative_time_label(action.get("start_time", "00:00"))
            dur        = action.get("duration_minutes")
            dur_str    = f", {dur} min" if dur else ""

        return {
            **{k: action.get(k) for k in
               ("mode", "start_time", "end_time", "power_kw", "cost", "reason")},
            "time_label":   time_label,
            "minutes_away": mu,
            "is_active":    is_active,
            "message": (
                f"{time_label}: {mode_str} {start_str}–{end_str}"
                f" ({power:.1f} kW, ${cost:.2f}{dur_str})"
            ),
        }

    # Active window always comes first
    for a in active_actions:
        try:
            notifications.append(_build_entry(a, is_active=True))
        except Exception:
            continue
        if len(notifications) >= limit:
            return notifications

    # Then upcoming
    for a in upcoming_actions:
        try:
            notifications.append(_build_entry(a, is_active=False))
        except Exception:
            continue
        if len(notifications) >= limit:
            break

    return notifications


def _build_summary(
    schedule_dict: Dict,
    indoor_temp: float,
    target_temp: float,
) -> Dict:
    return {
        "total_cost_24h":       round(schedule_dict.get("total_cost", 0), 3),
        "total_energy_24h_kwh": round(schedule_dict.get("total_energy_kwh", 0), 2),
        "comfort_score":        round(schedule_dict.get("comfort_score", 0), 1),
        "current_temp_c":       round(float(indoor_temp), 1),
        "target_temp_c":        target_temp,
        "engine":               schedule_dict.get("engine", "unknown"),
    }


def _map_hvac_status(mode: str) -> str:
    if mode in ("heat", "pre-heat", "heating", "pre_heat"):
        return "heating"
    if mode in ("cool", "pre-cool", "cooling", "pre_cool"):
        return "cooling"
    return "off"


def _get_state_or_error(username: str) -> tuple:
    """Return (state, user_id, house_data, error_dict | None)."""
    state = get_user_state(username)
    if state is None:
        return None, None, None, {"error": "User not found"}
    user_id = state.get("id")
    if user_id is None:
        return None, None, None, {"error": "User ID missing"}
    house = state.get("house")
    if not house:
        return None, None, None, {"error": "House data missing — submit house information first"}
    house_data = house.get("data", house) if isinstance(house, dict) else house
    return state, user_id, house_data, None


# ════════════════════════════════════════════════════════════════════════════
#  Public simulation functions
# ════════════════════════════════════════════════════════════════════════════

def run_simulation_step(username: str) -> Dict:
    """Basic single-step simulation (no HVAC schedule)."""
    state, user_id, house_data, err = _get_state_or_error(username)
    if err:
        return err

    raw_weather = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing"}
    weather_rows = _extract_weather_rows(raw_weather)
    if not weather_rows:
        return {"error": "Weather rows missing"}

    weather = _select_weather_row(weather_rows, datetime.now()) or weather_rows[0]
    if "temperature_2m" not in weather:
        return {"error": "temperature_2m missing from weather data"}

    outdoor_temp = float(weather["temperature_2m"])
    indoor_temp  = state.get("simulated_temp")
    if indoor_temp is None:
        indoor_temp = outdoor_temp
        update_simulated_temp(user_id, indoor_temp)

    result   = predict_temperature(
        house_data            = house_data,
        current_indoor_temp_c = float(indoor_temp),
        weather_data          = weather,
        timestep_minutes      = DEFAULT_TIMESTEP_MINUTES,
    )
    new_temp = result.get("new_temp_c", indoor_temp)
    update_simulated_temp(user_id, new_temp)

    return {
        "T_in_prev": round(float(indoor_temp), 2),
        "T_in_new":  round(float(new_temp), 2),
        "T_out":     outdoor_temp,
        "engine":    result.get("engine", "unknown"),
    }


def run_hvac_ai(username: str, target_temp_c: float = None) -> Dict:
    """
    Return the 24-hour HVAC schedule, refreshing notifications from the
    current time.

    Schedule regeneration policy
    ─────────────────────────────
    • target_temp_c explicitly supplied  → always regenerate (user changed setpoint)
    • existing schedule is stale (>1 h old or all runs exhausted) → regenerate
    • otherwise                          → reuse existing schedule, just
                                           re-slice notifications from *now*

    This stops the periodic frontend refresh (every 5 min) from wiping the
    in-progress run and restarting the plan from scratch.
    """
    state, user_id, house_data, err = _get_state_or_error(username)
    if err:
        return err

    raw_weather  = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing — fetch weather first"}
    weather_rows = _extract_weather_rows(raw_weather)
    if not weather_rows:
        return {"error": "Weather rows missing"}

    indoor_temp = state.get("simulated_temp")
    if indoor_temp is None:
        indoor_temp = float(weather_rows[0].get("temperature_2m", 20))
        update_simulated_temp(user_id, indoor_temp)

    target = _resolve_target_temp(
        user_id, state, house_data, target_temp_c,
        weather_rows=weather_rows,
        t_in=float(indoor_temp),
    )

    existing_schedule = state.get("hvac_sim")
    setpoint_changed  = target_temp_c is not None   # explicit caller value

    # ── Decide whether to regenerate ─────────────────────────────────────
    if setpoint_changed or _schedule_is_stale(existing_schedule):
        schedule = generate_hvac_schedule(
            house_data            = house_data,
            weather_rows          = weather_rows,
            current_indoor_temp_c = float(indoor_temp),
            personal_comfort      = target,
            target_temp_c         = target,
        )
        schedule_dict = schedule.to_dict()
        set_hvac_sim(user_id, schedule_dict)
        current_action = get_current_hvac_action(schedule)
    else:
        # Reuse the existing schedule — just build a fresh HVACSchedule
        # wrapper so get_current_hvac_action works with the dataclass API
        schedule_dict  = existing_schedule
        from .hvac_physics import HVACAction as _HVACAction
        _af = {f.name for f in _HVACAction.__dataclass_fields__.values()}
        _actions = [
            _HVACAction(**{k: v for k, v in a.items() if k in _af})
            for a in schedule_dict.get("actions", [])
        ]
        from .hvac_physics import HVACSchedule as _HVACSchedule
        schedule       = _HVACSchedule(
            actions          = _actions,
            total_cost       = schedule_dict.get("total_cost", 0),
            total_energy_kwh = schedule_dict.get("total_energy_kwh", 0),
            comfort_score    = schedule_dict.get("comfort_score", 0),
            generated_at     = schedule_dict.get("generated_at", ""),
        )
        current_action = get_current_hvac_action(schedule)

    current_hour   = datetime.now().hour
    notifications  = _format_notifications(schedule_dict, current_hour)
    summary        = _build_summary(schedule_dict, indoor_temp, target)

    # ── AI setpoint metadata (same shape as simulation step response) ─────
    rec = _get_ai_record(user_id)
    raw_comfort = house_data.get("personal_comfort", 22.0)
    comfort_c   = max(15.0, min(30.0, float(raw_comfort)))
    try:
        from .hvac_physics import _optimize_setpoint_physics
        _suggestion = _optimize_setpoint_physics(
            house_data, float(indoor_temp), comfort_c, 2.0,
            weather_rows, datetime.now().hour
        )
        suggested_sp  = _suggestion["optimal_setpoint_c"]
        suggested_why = _suggestion["strategy"]
        suggested_pre = _suggestion.get("pre_conditioning", False)
    except Exception:
        suggested_sp  = comfort_c
        suggested_why = None
        suggested_pre = False

    ai_setpoint_meta = {
        "enabled":                    rec.enabled,
        "setpoint_c":                 rec.ai_setpoint_c if rec.enabled else None,
        "strategy":                   rec.last_strategy  if rec.enabled else None,
        "source":                     rec.last_source    if rec.enabled else None,
        "pre_conditioning":           rec.pre_conditioning if rec.enabled else suggested_pre,
        "suggested_setpoint_c":       suggested_sp,
        "suggested_strategy":         suggested_why,
        "suggested_pre_conditioning": suggested_pre,
        "manual_override":            _manual_override_active(rec),
        "manual_override_c":          rec.manual_override_c if _manual_override_active(rec) else None,
        "manual_override_until": (
            rec.manual_override_until.isoformat()
            if _manual_override_active(rec) and rec.manual_override_until else None
        ),
    }

    return {
        "schedule": schedule_dict,
        "current_action": {
            "mode":     current_action.mode     if current_action else "off",
            "power_kw": current_action.power_kw if current_action else 0.0,
            "reason":   current_action.reason   if current_action else "No schedule",
        },
        "notifications":  notifications,
        "summary":        summary,
        "ai_setpoint":    ai_setpoint_meta,
        "regenerated":    setpoint_changed or _schedule_is_stale(existing_schedule),
    }


def run_simulation_step_with_hvac(
    username:     str,
    target_temp_c: float = None,
) -> Dict:
    """
    Run one 5-minute simulation step with full HVAC AI control.

    Auto-generates a schedule on first call.
    Tries Gemini for both schedule generation and step simulation;
    falls back to RC physics on any failure.
    """
    state, user_id, house_data, err = _get_state_or_error(username)
    if err:
        return err

    raw_weather  = state.get("weather")
    if not raw_weather:
        return {"error": "Weather data missing"}
    weather_rows = _extract_weather_rows(raw_weather)
    if not weather_rows:
        return {"error": "Weather rows missing"}

    weather_row = _select_weather_row(weather_rows, datetime.now()) or weather_rows[0]

    indoor_temp = state.get("simulated_temp")
    if indoor_temp is None:
        indoor_temp = float(weather_row.get("temperature_2m", 20))
        update_simulated_temp(user_id, indoor_temp)

    target = _resolve_target_temp(
        user_id, state, house_data, target_temp_c,
        weather_rows=weather_rows,
        t_in=float(indoor_temp),
    )

    # ── Resolve schedule (auto-generate if absent or stale) ─────────────
    hvac_schedule      = state.get("hvac_sim")
    schedule_generated = False
    notifications: List[Dict] = []
    summary: Optional[Dict]   = None
    current_hour = datetime.now().hour

    if _schedule_is_stale(hvac_schedule):
        try:
            schedule = generate_hvac_schedule(
                house_data            = house_data,
                weather_rows          = weather_rows,
                current_indoor_temp_c = float(indoor_temp),
                personal_comfort      = target,
                target_temp_c         = target,
            )
            hvac_schedule      = schedule.to_dict()
            set_hvac_sim(user_id, hvac_schedule)
            schedule_generated = True
            notifications      = _format_notifications(hvac_schedule, current_hour)
            summary            = _build_summary(hvac_schedule, indoor_temp, target)
        except Exception as exc:
            print(f"[HVAC AI] Schedule generation failed: {exc}")
            hvac_schedule = None
    else:
        notifications = _format_notifications(hvac_schedule, current_hour)
        summary       = _build_summary(hvac_schedule, indoor_temp, target)

    # ── Compute actual elapsed time for this step ───────────────────────
    # On the very first call use FIRST_STEP_SECONDS (60 s) instead of the
    # full 300 s MAX_TIMESTEP.  This prevents a visible temperature jump on
    # page mount while still advancing the simulation meaningfully.
    _now  = datetime.now()
    _last = _last_step_times.get(user_id)
    if _last is None:
        elapsed_s = FIRST_STEP_SECONDS
    else:
        elapsed_s = min((_now - _last).total_seconds(), MAX_TIMESTEP_SECONDS)
        elapsed_s = max(elapsed_s, 1.0)
    _last_step_times[user_id] = _now

    # ── Simulate step ────────────────────────────────────────────────────
    result = simulate_step_with_hvac(
        house_data            = house_data,
        current_indoor_temp_c = float(indoor_temp),
        target_temp_c         = target,
        weather_data          = weather_row,
        hvac_schedule         = hvac_schedule,
        personal_comfort      = target,
        dt_s                  = elapsed_s,
    )

    new_temp   = result.get("new_temp_c", indoor_temp)
    hvac_mode  = result.get("hvac_mode", "off")
    hvac_power = result.get("hvac_power_kw", 0.0)

    update_simulated_temp(user_id, new_temp)

    energy_kwh = result.get("hvac_energy_kwh", 0.0)
    rate       = result.get("electricity_rate", get_electricity_price(current_hour))
    cost       = result.get("cost_this_step", energy_kwh * rate)

    response = {
        "T_in_prev":          round(float(indoor_temp), 2),
        "T_in_new":           round(float(new_temp), 2),
        "T_out":              round(float(weather_row.get("temperature_2m", 20)), 2),
        "hvac_mode":          _map_hvac_status(hvac_mode),
        "hvac_power_kw":      round(float(hvac_power), 3),
        "hvac_energy_kwh":    round(float(energy_kwh), 4),
        "electricity_rate":   rate,
        "cost_this_step":     round(float(cost), 4),
        "target_temp":        target,
        "reason":             result.get("reason", ""),
        "has_schedule":       hvac_schedule is not None,
        "schedule_generated": schedule_generated,
        "engine":             result.get("engine", "unknown"),
        # Physics diagnostics (only present when engine == "physics")
        **{k: result[k] for k in
           ("q_conductive_w", "q_wind_w", "q_solar_w", "q_hvac_w", "q_total_w")
           if k in result},
    }

    # ── AI setpoint metadata ─────────────────────────────────────────────
    # Always compute a physics-based suggestion so the frontend can display
    # "AI suggests X°C" even when full AI mode is not enabled.
    rec = _get_ai_record(user_id)
    raw_comfort = house_data.get("personal_comfort", 22.0)
    comfort_c   = max(15.0, min(30.0, float(raw_comfort)))
    try:
        from .hvac_physics import _optimize_setpoint_physics
        _suggestion = _optimize_setpoint_physics(
            house_data, float(new_temp), comfort_c, 2.0,
            weather_rows, datetime.now().hour
        )
        suggested_sp  = _suggestion["optimal_setpoint_c"]
        suggested_why = _suggestion["strategy"]
        suggested_pre = _suggestion.get("pre_conditioning", False)
    except Exception:
        suggested_sp  = comfort_c
        suggested_why = None
        suggested_pre = False

    response["ai_setpoint"] = {
        "enabled":              rec.enabled,
        # Active AI mode values (when enabled=True)
        "setpoint_c":           rec.ai_setpoint_c if rec.enabled else None,
        "strategy":             rec.last_strategy  if rec.enabled else None,
        "source":               rec.last_source    if rec.enabled else None,
        "pre_conditioning":     rec.pre_conditioning if rec.enabled else suggested_pre,
        # Always-present physics suggestion (shown even in manual mode)
        "suggested_setpoint_c": suggested_sp,
        "suggested_strategy":   suggested_why,
        "suggested_pre_conditioning": suggested_pre,
        # Manual override info
        "manual_override":      _manual_override_active(rec),
        "manual_override_c":    rec.manual_override_c if _manual_override_active(rec) else None,
        "manual_override_until": (
            rec.manual_override_until.isoformat()
            if _manual_override_active(rec) and rec.manual_override_until else None
        ),
    }
    if notifications:
        response["notifications"] = notifications
    if summary:
        response["summary"] = summary

    return response


# ════════════════════════════════════════════════════════════════════════════
#  Setpoint management
# ════════════════════════════════════════════════════════════════════════════

def update_target_setpoint(username: str, target_temp_c: float) -> Dict:
    user_id = get_user_id(username)
    if user_id is None:
        return {"error": "User not found"}
    if not 15 <= target_temp_c <= 30:
        return {"error": "Target temperature must be between 15 °C and 30 °C"}
    set_target_setpoint(user_id, target_temp_c)
    return {
        "success":       True,
        "target_temp_c": target_temp_c,
        "message":       f"Target temperature set to {target_temp_c} °C",
    }


def get_current_setpoint(username: str) -> Dict:
    state = get_user_state(username)
    if state is None:
        return {"error": "User not found"}
    user_id = state.get("id")
    saved = state.get("target_setpoint")
    if saved is not None:
        return {"target_temp_c": saved, "source": "saved"}
    house = state.get("house")
    if house:
        hd = house.get("data", house) if isinstance(house, dict) else house
        comfort = hd.get("personal_comfort")
        if comfort is not None:
            t = max(15.0, min(30.0, float(comfort)))
            set_target_setpoint(user_id, t)
            return {"target_temp_c": t, "source": "personal_comfort"}
    return {"target_temp_c": 22.0, "source": "default"}


# ════════════════════════════════════════════════════════════════════════════
#  Schedule utilities
# ════════════════════════════════════════════════════════════════════════════

def get_hvac_schedule_summary(username: str) -> Dict:
    state = get_user_state(username)
    if state is None:
        return {"error": "User not found"}
    sched = state.get("hvac_sim")
    if sched is None:
        return {"error": "No HVAC schedule found — run HVAC AI first"}
    mode_counts = {m: 0 for m in ("heat", "cool", "pre-heat", "pre-cool", "off")}
    for a in sched.get("actions", []):
        m = a.get("mode", "off")
        mode_counts[m if m in mode_counts else "off"] += 1
    return {
        "generated_at":     sched.get("generated_at"),
        "total_cost":       sched.get("total_cost", 0),
        "total_energy_kwh": sched.get("total_energy_kwh", 0),
        "comfort_score":    sched.get("comfort_score", 0),
        "hours_by_mode":    mode_counts,
        "actions_count":    len(sched.get("actions", [])),
        "engine":           sched.get("engine", "unknown"),
    }


def apply_temporary_adjustment(
    username:         str,
    adjustment_c:     float,
    duration_minutes: int = 60,
) -> Dict:
    """
    Shift the setpoint by adjustment_c for duration_minutes.
    Uses GenAI for the recommendation if available; falls back to
    pure physics reasoning.
    """
    state, user_id, house_data, err = _get_state_or_error(username)
    if err:
        return err

    indoor_temp   = state.get("simulated_temp", 22.0)
    base_setpoint = get_current_setpoint(username).get("target_temp_c", 22.0)
    temp_target   = max(15.0, min(30.0, base_setpoint + adjustment_c))

    raw_weather   = state.get("weather")
    weather_rows  = _extract_weather_rows(raw_weather) if raw_weather else []
    current_w     = _select_weather_row(weather_rows, datetime.now()) or {}
    outdoor_temp  = float(current_w.get("temperature_2m", 15))
    current_hour  = datetime.now().hour
    rate          = get_electricity_price(current_hour)

    # Try GenAI for recommendation; fall back to physics
    ai = None
    try:
        from .hvac_physics import _call_genai
        _lines = [
            "Recommend whether to apply a temporary HVAC adjustment.",
            f"Indoor: {indoor_temp:.1f}C  Setpoint: {base_setpoint:.1f}C",
            f"Adjustment: {adjustment_c:+.1f}C -> new target {temp_target:.1f}C for {duration_minutes} min",
            f"Outdoor: {outdoor_temp}C  Rate: ${rate}/kWh",
            f"HVAC: {house_data.get('hvac_type', 'central')}",
            "",
            "Return JSON only: apply_now bool, reason str, hvac_mode str,",
            "estimated_time_to_target_minutes num, estimated_energy_kwh num,",
            "estimated_cost num, recommended_action str, comfort_impact str",
        ]
        ai = _call_genai("\n".join(_lines))
    except Exception:
        ai = None

    if ai is None:
        # Physics-based recommendation using proper house data
        need_heat = temp_target > float(indoor_temp)
        from .hvac_physics import (
            _cop, _thermal_mass, _house_geometry, 
            _hvac_capacity, _insulation_params
        )
        
        # Use actual house HVAC capacity (not hardcoded)
        hvac_cap_kw = (
            _hvac_capacity(house_data, "heating") if need_heat 
            else _hvac_capacity(house_data, "cooling")
        )
        
        # Get proper COP based on outdoor temperature
        cop_val = _cop(outdoor_temp, float(house_data.get("cop_base", 3.5)))
        q_w = hvac_cap_kw * 1000.0 * cop_val  # Heat/cool capacity in Watts
        
        # Calculate thermal mass for accurate estimation
        geo = _house_geometry(house_data)
        c_home = _thermal_mass(geo["floor_area_m2"])
        
        # Time to reach target (minutes) = mass × temp_delta / power
        temp_delta = abs(temp_target - float(indoor_temp))
        dt_min = max(1.0, (temp_delta * c_home) / max(q_w * 60.0, 1.0))
        energy_kwh = hvac_cap_kw * dt_min / 60.0
        
        ai = {
            "apply_now":                        True,
            "reason":                           f"RC physics estimate: {geo['floor_area_m2']:.0f}m² home with {c_home/1e6:.1f}MJ/°C thermal mass",
            "hvac_mode":                        "heat" if need_heat else "cool",
            "estimated_time_to_target_minutes": round(dt_min, 1),
            "estimated_energy_kwh":             round(energy_kwh, 3),
            "estimated_cost":                   round(energy_kwh * rate, 4),
            "recommended_action":               f"{'Heating' if need_heat else 'Cooling'} {hvac_cap_kw:.1f}kW system to {temp_target:.1f}°C",
            "comfort_impact":                   "positive" if abs(adjustment_c) <= 2.0 else "moderate",
        }

    if ai.get("apply_now", True):
        set_target_setpoint(user_id, temp_target)

    return {
        "success":            True,
        "adjustment_applied": ai.get("apply_now", True),
        "base_target_c":      base_setpoint,
        "temporary_target_c": temp_target,
        "adjustment_c":       adjustment_c,
        "duration_minutes":   duration_minutes,
        "current_indoor_c":   indoor_temp,
        "outdoor_temp_c":     outdoor_temp,
        "ai_recommendation":  ai,
        "timestamp":          datetime.now().isoformat(),
    }

# ════════════════════════════════════════════════════════════════════════════
#  AI Setpoint Control  — Public API
#  These functions are the main new surface added on top of the original code.
# ════════════════════════════════════════════════════════════════════════════

def enable_ai_setpoint_mode(
    username:     str,
    comfort_tol:  float = 2.0,
) -> Dict:
    """
    Enable AI-managed setpoint optimisation for this user.

    Parameters
    ----------
    username    : the user whose HVAC we're controlling
    comfort_tol : half-width of acceptable comfort band around personal_comfort
                  (default ±2 °C).  The AI will never exceed this range.

    Once enabled, every call to run_simulation_step_with_hvac or run_hvac_ai
    will let the AI choose the setpoint (refreshed every 30 min) instead of
    using the fixed saved value.

    Manual override is still possible via set_manual_override(); it
    automatically expires and AI control resumes.
    """
    state, user_id, house_data, err = _get_state_or_error(username)
    if err:
        return err

    # Extract user's comfort centre from house data for AI band calculation
    raw_comfort = house_data.get("personal_comfort", 22.0)
    comfort_c = max(15.0, min(30.0, float(raw_comfort)))
    
    rec              = _get_ai_record(user_id)
    rec.enabled      = True
    rec.comfort_tol  = max(0.5, min(4.0, float(comfort_tol)))
    # Force an immediate recompute on the next step
    rec.last_computed = None

    return {
        "success":      True,
        "ai_mode":      "enabled",
        "comfort_center_c":  comfort_c,
        "comfort_band":      f"[{comfort_c - rec.comfort_tol:.1f}°C, {comfort_c + rec.comfort_tol:.1f}°C]",
        "comfort_tol":  rec.comfort_tol,
        "message":      (
            f"AI setpoint mode enabled. Your comfort centre: {comfort_c:.1f}°C ±{rec.comfort_tol:.1f}°C. "
            f"Setpoint will be optimised every 30 min to shift around this band using TOU pricing & weather."
        ),
    }


def disable_ai_setpoint_mode(username: str) -> Dict:
    """
    Disable AI-managed setpoint and return control to the manually-saved value.
    The last AI-chosen setpoint is written to the DB so the thermostat doesn't jump.
    """
    state, user_id, house_data, err = _get_state_or_error(username)
    if err:
        return err

def disable_ai_setpoint_mode(username: str) -> Dict:
    """
    Disable AI-managed setpoint and return control to the manually-saved value.
    The last AI-chosen setpoint is written to the DB so the thermostat doesn't jump.
    User's comfort centre (from house settings) is included for reference.
    """
    state, user_id, house_data, err = _get_state_or_error(username)
    if err:
        return err

    rec         = _get_ai_record(user_id)
    last_sp     = rec.ai_setpoint_c
    rec.enabled = False
    rec.manual_override_c     = None
    rec.manual_override_until = None

    # Persist the last AI setpoint so the controller keeps it as the new manual value
    set_target_setpoint(user_id, last_sp)
    
    # Include comfort centre for user awareness
    raw_comfort = house_data.get("personal_comfort", 22.0)
    comfort_c = max(15.0, min(30.0, float(raw_comfort)))

    return {
        "success":         True,
        "ai_mode":         "disabled",
        "comfort_center_c":     comfort_c,
        "retained_setpoint_c": last_sp,
        "message": (
            f"AI setpoint mode disabled. Setpoint held at {last_sp:.1f}°C "
            f"(your comfort centre is {comfort_c:.1f}°C). Adjust manually as needed."
        ),
    }


def set_manual_override(
    username:          str,
    setpoint_c:        float,
    duration_minutes:  int = 60,
) -> Dict:
    """
    Temporarily override the AI setpoint for `duration_minutes`.

    Works in both AI mode and manual mode:
    • AI mode   → override expires after duration_minutes, AI resumes.
    • Manual    → behaves exactly like update_target_setpoint (no expiry needed,
                  but still accepted for API consistency).

    Parameters
    ----------
    setpoint_c       : desired temperature (°C), clamped to [15, 30]
    duration_minutes : how long the override holds (AI mode only)
    """
    state, user_id, house_data, err = _get_state_or_error(username)
    if err:
        return err

    setpoint_c = max(15.0, min(30.0, float(setpoint_c)))
    rec        = _get_ai_record(user_id)
    
    # Get user's comfort centre for context
    raw_comfort = house_data.get("personal_comfort", 22.0)
    comfort_c = max(15.0, min(30.0, float(raw_comfort)))
    drift_from_comfort = setpoint_c - comfort_c

    from datetime import timedelta
    expiry = datetime.now() + timedelta(minutes=duration_minutes)

    rec.manual_override_c     = setpoint_c
    rec.manual_override_until = expiry

    # Also update DB so other callers (e.g. bare get_current_setpoint) see it
    set_target_setpoint(user_id, setpoint_c)

    return {
        "success":              True,
        "override_setpoint_c":  setpoint_c,
        "comfort_center_c":     comfort_c,
        "drift_from_comfort_c": round(drift_from_comfort, 1),
        "duration_minutes":     duration_minutes,
        "expires_at":           expiry.isoformat(),
        "ai_mode_active":       rec.enabled,
        "message": (
            f"Setpoint manually set to {setpoint_c:.1f}°C (drift: {drift_from_comfort:+.1f}°C from your {comfort_c:.1f}°C comfort) "
            f"for {duration_minutes} min. "
            + (f"AI resumes at {expiry.strftime('%H:%M')}." if rec.enabled
               else "AI mode is off — override persists indefinitely.")
        ),
    }


def cancel_manual_override(username: str) -> Dict:
    """
    Cancel an active manual override early, returning control to the AI immediately.
    Has no effect when AI mode is disabled.
    """
    state, user_id, house_data, err = _get_state_or_error(username)
    if err:
        return err

    rec = _get_ai_record(user_id)
    was_active = _manual_override_active(rec)
    rec.manual_override_c     = None
    rec.manual_override_until = None

    return {
        "success":      True,
        "was_active":   was_active,
        "ai_mode":      rec.enabled,
        "message": (
            "Manual override cancelled — AI setpoint resumes immediately."
            if rec.enabled else
            "Manual override cleared (AI mode is off; setpoint unchanged)."
        ),
    }


def get_ai_setpoint_status(username: str) -> Dict:
    """
    Return the full AI setpoint state for this user.
    Useful for the frontend to display mode, current AI setpoint, override status, etc.
    """
    state = get_user_state(username)
    if state is None:
        return {"error": "User not found"}

    user_id = state.get("id")
    if user_id is None:
        return {"error": "User ID missing"}

    rec        = _get_ai_record(user_id)
    now        = datetime.now()
    override   = _manual_override_active(rec)

    minutes_until_refresh: Optional[float] = None
    if rec.last_computed is not None:
        age_s = (now - rec.last_computed).total_seconds()
        minutes_until_refresh = max(0.0, (_SETPOINT_REFRESH_INTERVAL_S - age_s) / 60.0)
        minutes_until_refresh = round(minutes_until_refresh, 1)

    minutes_until_resume: Optional[float] = None
    if override and rec.manual_override_until:
        minutes_until_resume = round(
            (rec.manual_override_until - now).total_seconds() / 60.0, 1
        )

    return {
        "ai_mode_enabled":       rec.enabled,
        "comfort_tol_c":         rec.comfort_tol,
        "current_ai_setpoint_c": rec.ai_setpoint_c if rec.enabled else None,
        "last_strategy":         rec.last_strategy,
        "last_source":           rec.last_source,
        "pre_conditioning":      rec.pre_conditioning,
        "last_computed":         rec.last_computed.isoformat() if rec.last_computed else None,
        "minutes_until_refresh": minutes_until_refresh,
        "manual_override_active": override,
        "manual_override_c":     rec.manual_override_c if override else None,
        "manual_override_expires": (
            rec.manual_override_until.isoformat()
            if override and rec.manual_override_until else None
        ),
        "minutes_until_ai_resumes": minutes_until_resume,
    }