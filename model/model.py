"""
smart_hvac.py

Simple standalone HVAC simulation + "smart" on/off controller prototype.

How to use:
  1. Place this file in a folder.
  2. Optionally add:
     - house_variables.txt  (simple key: value pairs)
     - weather_7days.txt    (one line per hour; each line starting with an ISO-like datetime,
                            followed by numeric columns - at minimum a temperature value)
  3. Run: python smart_hvac.py
  4. Outputs:
     - hvac_hourly.csv     (hourly log)
     - hvac_intervals.csv  (aggregated contiguous on-intervals)
"""

import os
import re
import math
from datetime import datetime, timedelta
import pandas as pd

# --------------------------
# User-adjustable assumptions
# --------------------------
ASSUMPTIONS = {
    # house geometry / thermal
    "home_size_m2": 123.0,
    "insulation_quality": "good",   # "good" or otherwise (affects UA)
    "UA_per_m2_good": 1.8,          # W/m2K for "good" insulation
    "UA_per_m2_poor": 2.5,          # W/m2K for other insulation

    # thermal mass (lumped) J/K
    "thermal_mass_J_per_K": 3.0e6,

    # HVAC (heat pump)
    "hvac_max_thermal_W": 5000.0,   # maximum delivered thermal power (W)
    # COP model (heating) given outside temperature (degC)
    "cop_func": lambda Tout: max(1.5, 3.0 + 0.03 * (Tout - 10.0)),

    # setpoints & occupancy (local time)
    "occupied_setpoint_C": 20.0,
    "unoccupied_setback_C": 17.0,
    "occupancy_start_hour": 7,      # inclusive
    "occupancy_end_hour": 22,       # exclusive (i.e., last occupied hour is 21)

    # time step used in simulation
    "time_step_seconds": 3600,
}

# --------------------------
# File reading helpers
# --------------------------
def read_house_variables(path="house_variables.txt"):
    """
    Read house variables file of simple "key: value" lines.
    Returns dict of keys -> values (strings).
    """
    if os.path.exists(path):
        txt = open(path, "r", encoding="utf-8").read()
    else:
        # fallback defaults (from user's earlier message)
        txt = (
            "home_size: 123\n"
            "insulation_quality: good\n"
            "hvac_type: heat_pump\n"
            "occupancy_start: 123\n"
            "occupancy_end: 123\n"
        )
    vars = {}
    for line in txt.splitlines():
        m = re.match(r"\s*([^:]+)\s*:\s*(.+)$", line)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            vars[key] = val
    return vars

def read_weather(path="weather_7days.txt"):
    """
    Read a plain-text weather file. Expects lines that start with an ISO-like datetime,
    possibly including timezone offset. After the datetime there should be numeric columns;
    we will parse at least the first numeric after datetime as temperature_2m (°C).
    If file not found, generate a synthetic 72-hour weather series for demo/runability.
    Returns a pandas DataFrame with columns: datetime (pd.Timestamp), temperature_2m (float)
    """
    if os.path.exists(path):
        lines = [l for l in open(path, "r", encoding="utf-8").read().splitlines() if l.strip()]
        rows = []
        dt_re = re.compile(
            r"^(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2})?)"
        )  # captures 2025-10-12 00:00:00-04:00 etc.
        for line in lines:
            m = dt_re.match(line.strip())
            if not m:
                # try more permissive - take first whitespace-delimited token as datetime
                tokens = re.split(r"\s+", line.strip())
                dt_token = tokens[0] if tokens else None
            else:
                dt_token = m.group(1)
            if not dt_token:
                continue
            # remaining numbers
            remainder = line.strip()[len(dt_token):].strip()
            numeric_tokens = re.split(r"\s+", remainder) if remainder else []
            # parse datetime safely with pandas
            try:
                dt = pd.to_datetime(dt_token)
            except Exception:
                # try replacing space with T
                try:
                    dt = pd.to_datetime(dt_token.replace(" ", "T"))
                except Exception:
                    continue
            # temperature is first numeric token after datetime
            temp = float(numeric_tokens[0]) if numeric_tokens and re.match(r"[-+]?\d", numeric_tokens[0]) else float("nan")
            rows.append({"datetime": pd.Timestamp(dt), "temperature_2m": temp, "raw_line": line.strip()})
        if not rows:
            raise RuntimeError(f"weather file found but no parsable lines in {path}")
        df = pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)
        # drop raw_line column but keep for debugging if needed
        df = df[["datetime", "temperature_2m"]]
        return df
    else:
        # create synthetic data for 72 hours to allow script to run without a real file
        start = datetime(2025, 10, 12, 0, 0)
        rows = []
        for h in range(72):
            dt = start + timedelta(hours=h)
            # synthetic but plausible temps (cool night, warmer day)
            temp = 10.0 + 6.0 * math.sin((h / 24.0) * 2 * math.pi) - 1.8 * math.sin((h / 6.0) * 2 * math.pi)
            rows.append({"datetime": pd.Timestamp(dt), "temperature_2m": round(temp, 3)})
        return pd.DataFrame(rows)

# --------------------------
# Thermal model + controller
# --------------------------
def run_simulation(df_weather, house_vars, assumptions):
    """
    Run a simple hourly lumped-capacitance thermal model:
      Cth * dT/dt = -UA*(T_in - T_out) + Q_hvac
    Controller rule:
      - if indoor < setpoint -> compute required thermal to reach setpoint
      - evaluate COP now and average COP next 3 hours
      - run now if COP_now >= avg_future_COP OR required power is large (>80% hvac max)
    """
    area = float(house_vars.get("home_size", assumptions["home_size_m2"]))
    ins_q = str(house_vars.get("insulation_quality", assumptions["insulation_quality"])).lower()
    UA_per_m2 = assumptions["UA_per_m2_good"] if ins_q.startswith("g") else assumptions["UA_per_m2_poor"]
    UA = UA_per_m2 * area           # W/K
    Cth = assumptions["thermal_mass_J_per_K"]
    hvac_max_W = assumptions["hvac_max_thermal_W"]
    dt_seconds = assumptions["time_step_seconds"]
    cop_func = assumptions["cop_func"]
    occ_start = assumptions["occupancy_start_hour"]
    occ_end = assumptions["occupancy_end_hour"]
    setpoint_occ = assumptions["occupied_setpoint_C"]
    setpoint_unocc = assumptions["unoccupied_setback_C"]

    # initialize indoor temp to first outside temp or to occupied setpoint - small offset
    if df_weather["temperature_2m"].notnull().any():
        Tin = float(df_weather["temperature_2m"].iloc[0])
    else:
        Tin = setpoint_unocc

    rows = []
    n = len(df_weather)
    for idx, r in df_weather.iterrows():
        tstamp = r["datetime"]
        Tout = float(r["temperature_2m"]) if not pd.isna(r["temperature_2m"]) else 10.0
        hour = int(tstamp.hour)
        occupied = (occ_start <= hour < occ_end)
        setpoint = setpoint_occ if occupied else setpoint_unocc

        # COP at current hour
        COP_now = float(cop_func(Tout))

        # required thermal to reach setpoint in one timestep (J)
        dT_needed = setpoint - Tin
        energy_needed_J = max(0.0, Cth * dT_needed)
        power_needed_W = energy_needed_J / dt_seconds

        # loss to outside (positive means heat loss from inside to outside)
        Q_loss_W = UA * (Tin - Tout)

        # Predict avg COP over next 3 hours (if available)
        future_cops = []
        for h in range(1, 4):
            if idx + h < n:
                Tout_future = float(df_weather.loc[idx + h, "temperature_2m"])
                future_cops.append(float(cop_func(Tout_future)))
        avg_future_cop = sum(future_cops) / len(future_cops) if future_cops else COP_now

        hvac_on = False
        hvac_thermal_W = 0.0
        elec_kW = 0.0

        if Tin < setpoint - 0.05:
            # crude required W: energy needed plus any negative loss (if house tends to lose heat)
            required_W = power_needed_W + max(0.0, -Q_loss_W)
            # decision rule
            if (COP_now >= avg_future_cop) or (required_W > 0.8 * hvac_max_W):
                hvac_on = True
                hvac_thermal_W = min(hvac_max_W, required_W)
                elec_kW = (hvac_thermal_W / max(COP_now, 0.001)) / 1000.0
            else:
                hvac_on = False
        else:
            hvac_on = False

        # thermal balance for the hour
        net_Q_W = -UA * (Tin - Tout) + (hvac_thermal_W if hvac_on else 0.0)
        dT = (net_Q_W * dt_seconds) / Cth
        Tin_next = Tin + dT

        rows.append({
            "datetime": pd.Timestamp(tstamp),
            "date": tstamp.date().isoformat(),
            "time": tstamp.time().isoformat(),
            "outside_T_C": round(Tout, 3),
            "inside_T_C_start": round(Tin, 3),
            "setpoint_C": setpoint,
            "occupied": bool(occupied),
            "hvac_on": bool(hvac_on),
            "hvac_thermal_kW": round(hvac_thermal_W / 1000.0, 4),
            "elec_kW": round(elec_kW, 4),
            "COP": round(COP_now, 3),
        })

        Tin = Tin_next

    df_out = pd.DataFrame(rows)
    return df_out

# --------------------------
# Postprocessing: intervals
# --------------------------
def extract_on_intervals(df_hourly):
    """
    Collapse contiguous hvac_on True rows into intervals.
    Each interval row: date, start_time, end_time, outside_T_start_C, inside_T_start_C, total_elec_kWh, total_thermal_kWh, hours
    """
    intervals = []
    cur = None
    for _, row in df_hourly.iterrows():
        if row["hvac_on"]:
            if cur is None:
                cur = {
                    "date": row["date"],
                    "start_time": row["time"],
                    "end_time": row["time"],
                    "outside_T_start_C": row["outside_T_C"],
                    "inside_T_start_C": row["inside_T_C_start"],
                    "total_elec_kWh": float(row["elec_kW"]),
                    "total_thermal_kWh": float(row["hvac_thermal_kW"]),
                    "hours": 1
                }
            else:
                cur["end_time"] = row["time"]
                cur["total_elec_kWh"] += float(row["elec_kW"])
                cur["total_thermal_kWh"] += float(row["hvac_thermal_kW"])
                cur["hours"] += 1
        else:
            if cur is not None:
                # finalize and append
                cur["total_elec_kWh"] = round(cur["total_elec_kWh"], 4)
                cur["total_thermal_kWh"] = round(cur["total_thermal_kWh"], 4)
                intervals.append(cur)
                cur = None
    if cur is not None:
        cur["total_elec_kWh"] = round(cur["total_elec_kWh"], 4)
        cur["total_thermal_kWh"] = round(cur["total_thermal_kWh"], 4)
        intervals.append(cur)
    if not intervals:
        return pd.DataFrame(columns=[
            "date", "start_time", "end_time", "outside_T_start_C",
            "inside_T_start_C", "total_elec_kWh", "total_thermal_kWh", "hours"
        ])
    return pd.DataFrame(intervals)

# --------------------------
# Main execution
# --------------------------
def main():
    print("smart_hvac.py starting...")

    # Read inputs
    house_vars = read_house_variables("house_variables.txt")
    try:
        df_weather = read_weather("weather_7days.txt")
    except Exception as e:
        print("Error reading weather file:", e)
        return

    # Run simulation
    df_hourly = run_simulation(df_weather, house_vars, ASSUMPTIONS)
    df_intervals = extract_on_intervals(df_hourly)

    # Output CSVs in current working directory
    out_hourly = os.path.join(os.getcwd(), "hvac_hourly.csv")
    out_intervals = os.path.join(os.getcwd(), "hvac_intervals.csv")
    df_hourly.to_csv(out_hourly, index=False)
    df_intervals.to_csv(out_intervals, index=False)

    # Simple stats
    total_hours = len(df_hourly)
    hours_on = int(df_hourly["hvac_on"].sum())
    total_elec_kWh = df_hourly["elec_kW"].sum()
    total_thermal_kWh = df_hourly["hvac_thermal_kW"].sum()
    avg_cop = df_hourly.loc[df_hourly["hvac_on"], "COP"].mean() if df_hourly["hvac_on"].any() else float("nan")

    print(f"Wrote hourly CSV:      {out_hourly}")
    print(f"Wrote intervals CSV:   {out_intervals}")
    print(f"Simulated hours: {total_hours}, HVAC ON hours: {hours_on}")
    print(f"Total elec (kWh): {total_elec_kWh:.3f}, Total thermal (kWh): {total_thermal_kWh:.3f}, Average COP while on: {avg_cop:.3f}")

    # show the first few intervals if any
    if not df_intervals.empty:
        print("\nSample on-intervals (first 10):")
        print(df_intervals.head(10).to_string(index=False))
    else:
        print("\nNo HVAC-on intervals found in simulation.")

if __name__ == "__main__":
    main()
