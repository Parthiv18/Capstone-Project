from pathlib import Path
import sqlite3
import json
import re
from datetime import datetime, timedelta
import pandas as pd


# ============================
#  DATABASE PATH
# ============================
def get_db_path() -> Path:
    base = Path(__file__).resolve().parent.parent
    db_path = base / "weather-app" / "database" / "users.db"
    return db_path


def fetch_all_users(db_path: Path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT username, user_weather, user_house FROM users")
    rows = cur.fetchall()
    conn.close()
    return rows


# ============================
#  HOUSE PARSER
# ============================
def parse_house(text: str) -> dict:
    if not text:
        return {}

    # Accept JSON input
    try:
        if text.strip().startswith("{"):
            return json.loads(text)
    except:
        pass

    # Accept "key: value" text format
    d = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", v):
            v = float(v)
        d[k] = v
    return d


# ============================
#  WEATHER PARSER
# ============================
def parse_weather(text: str) -> pd.DataFrame:
    if not text:
        return pd.DataFrame()

    try:
        obj = json.loads(text)
        rows = obj.get("rows", obj if isinstance(obj, list) else None)

        parsed = []
        if rows:
            for r in rows:
                dt = r.get("date")
                if not dt:
                    continue
                try:
                    dt = datetime.fromisoformat(dt.replace(" EST", ""))
                except:
                    continue

                parsed.append({
                    "date": dt,
                    "temp": float(r.get("temperature_2m", 0)),
                    "apparent": float(r.get("apparent_temperature", 0)),
                    "humidity": float(r.get("humidity_2m", 50)),
                    "solar": float(r.get("solar_radiation", 0)),
                    "windspeed_10m": float(r.get("windspeed_10m", 0)),
                    "dew_point_2m": float(r.get("dew_point_2m", 0)),
                    "precipitation": float(r.get("precipitation", 0)),
                    "rain": float(r.get("rain", 0)),
                    "snowfall": float(r.get("snowfall", 0)),
                })

        return pd.DataFrame(parsed)

    except Exception:
        return pd.DataFrame()


# ============================
#  PREDICTIVE HVAC MODEL (Option C)
# ============================
def compute_hvac_intervals(df: pd.DataFrame, house_vars: dict, merge: bool = True) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["date", "time on", "time off", "hvac temp set", "status"])

    df = df.sort_values("date").reset_index(drop=True)

    # -----------------------------
    # User Settings
    # -----------------------------
    desired = float(house_vars.get("personal_comfort", 22))

    insulation_map = {"poor": 1.6, "average": 1.0, "good": 0.75, "excellent": 0.55}
    insulation = insulation_map.get(str(house_vars.get("insulation_quality", "average")).lower(), 1.0)

    home_size = float(house_vars.get("home_size", 100))
    size_factor = min(1.3, max(0.8, home_size / 1000))

    hvac_age = float(house_vars.get("hvac_age", 5))
    hvac_eff = max(0.75, (12 - hvac_age) / 12)

    occupancy = str(house_vars.get("occupancy", "home_daytime"))
    occupancy_band = {"home_daytime": 0.65, "away_daytime": 1.3, "night": 1.1}.get(occupancy, 1.0)

    current_mode = "off"
    hvac_setpoints = []
    hvac_statuses = []

    def safe_temp(i):
        return df["temp"].iloc[i] if i < len(df) else df["temp"].iloc[-1]


    # ===============================
    # MAIN HOURLY LOOP
    # ===============================
    for i in range(len(df)):
        row = df.loc[i]

        T = row["temp"]
        apparent = row["apparent"]
        humidity = row["humidity"]
        dew = row["dew_point_2m"]
        solar = row["solar"]
        wind = row["windspeed_10m"]
        precipitation = row["precipitation"]

        # -----------------------------------------
        # 1) Effective Temperature (feels like)
        # -----------------------------------------
        effective_temp = (
            0.45 * T +
            0.35 * apparent +
            0.10 * (T - humidity / 12) +
            0.10 * (dew - 2)
        )
        effective_temp -= wind * 0.03
        effective_temp -= precipitation * 0.45
        if solar > 150:
            effective_temp += 0.3


        # -----------------------------------------
        # 2) Predictive Trend
        # -----------------------------------------
        T_now = T
        T_1h = safe_temp(i + 1)
        T_3h = safe_temp(i + 3)
        T_6h = safe_temp(i + 6)
        T_12h = safe_temp(i + 12)

        trend = (
            0.45*(T_1h - T_now) +
            0.30*(T_3h - T_now) +
            0.15*(T_6h - T_now) +
            0.10*(T_12h - T_now)
        )

        predictive_temp = effective_temp + trend * insulation * (2 - hvac_eff) * size_factor


        # -----------------------------------------
        # 3) Comfort Band by Time of Day
        # -----------------------------------------
        hour = row["date"].hour
        if 6 <= hour < 10:
            comfort = 0.55 * occupancy_band
        elif 10 <= hour < 22:
            comfort = 1.0 * occupancy_band
        else:
            comfort = 1.4 * occupancy_band

        hysteresis = comfort * 0.5


        # -----------------------------------------
        # 4) Decide HVAC Mode
        # -----------------------------------------
        if current_mode == "heating":
            if predictive_temp >= desired - (comfort - hysteresis):
                current_mode = "off"

        elif current_mode == "cooling":
            if predictive_temp <= desired + (comfort - hysteresis):
                current_mode = "off"

        else:
            if predictive_temp <= desired - comfort:
                current_mode = "heating"
            elif predictive_temp >= desired + comfort:
                current_mode = "cooling"
            else:
                current_mode = "off"


        # -----------------------------------------
        # 5) Raw Setpoint Calculation
        # -----------------------------------------
        if current_mode == "heating":
            setpoint = desired \
                - 0.25 \
                - 0.015 * wind \
                - 0.18 * trend \
                + (-0.15 if humidity > 75 else 0)

        elif current_mode == "cooling":
            setpoint = desired \
                + 0.55 \
                - 0.18 * trend \
                + (0.25 if humidity > 70 else 0) \
                + (solar / 400) * 0.4

        else:
            setpoint = ""


        # -----------------------------------------
        # 6) Dynamic Weather-Based Clamp (SMART LIMIT)
        # -----------------------------------------
        if isinstance(setpoint, (int, float)):
            # colder → higher max heat
            dynamic_heat_max = 22 + max(0, (10 - T) * 0.10)
            dynamic_heat_max = min(25.5, dynamic_heat_max)
            dynamic_heat_max += min(1.0, wind * 0.04 + precipitation * 0.2)

            # hotter → lower cooling minimum
            dynamic_cool_min = max(18, 20 - max(0, (apparent - 25) * 0.05))

            if current_mode == "heating":
                setpoint = min(dynamic_heat_max, max(desired - 4, setpoint))

            if current_mode == "cooling":
                setpoint = max(dynamic_cool_min, min(26, setpoint))


        # -----------------------------------------
        # 7) ON/OFF Decision
        # -----------------------------------------
        if current_mode == "off":
            hvac_status = "off"
        else:
            diff = abs(predictive_temp - desired)
            hvac_status = "on" if diff > comfort * 0.45 else "off"

        hvac_setpoints.append(setpoint)
        hvac_statuses.append(hvac_status)


    # ===============================
    # HOURLY DF
    # ===============================
    hourly = []
    for i in range(len(df)):
        dt = df.loc[i, "date"]
        end_dt = dt + timedelta(hours=1)
        sp = hvac_setpoints[i]

        hourly.append({
            "date": dt.date(),
            "time on": dt.strftime("%H:%M"),
            "time off": end_dt.strftime("%H:%M"),
            "hvac temp set": round(sp, 2) if isinstance(sp, (int, float)) else "",
            "status": hvac_statuses[i],
        })

    hourly_df = pd.DataFrame(hourly)
    if not merge:
        return hourly_df


    # ===============================
    # MERGE SAME STATUS INTERVALS
    # ===============================
    intervals = []
    start = 0
    cur = hourly_df.iloc[0]["status"]

    def build_interval(s, e, status):
        start_dt = df.loc[s, "date"]
        end_dt = df.loc[e, "date"] + timedelta(hours=1)
        vals = [v for v in hvac_setpoints[s:e + 1] if isinstance(v, (int, float))]
        sp = round(sum(vals) / len(vals), 2) if vals else ""
        return {
            "date": start_dt.date(),
            "time on": start_dt.strftime("%H:%M"),
            "time off": end_dt.strftime("%H:%M"),
            "hvac temp set": sp,
            "status": status,
        }

    for i in range(1, len(hourly_df)):
        if hourly_df.iloc[i]["status"] != cur:
            intervals.append(build_interval(start, i - 1, cur))
            start = i
            cur = hourly_df.iloc[i]["status"]

    intervals.append(build_interval(start, len(hourly_df) - 1, cur))
    return pd.DataFrame(intervals)


# ============================
#  EXPORT TO EXCEL
# ============================
def generate_schedules():
    db_path = get_db_path()
    rows = fetch_all_users(db_path)
    output_excel = Path("model") / "hvac_schedules.xlsx"

    sheets = {}

    for username, user_weather, user_house in rows:
        house_vars = parse_house(user_house or "")
        df_weather = parse_weather(user_weather or "")

        merged = compute_hvac_intervals(df_weather, house_vars, merge=True)
        hourly = compute_hvac_intervals(df_weather, house_vars, merge=False)

        # Format dates
        def fmt(df_in):
            if not df_in.empty and "date" in df_in.columns:
                df_in = df_in.copy()
                df_in["date"] = df_in["date"].apply(lambda d: d.strftime("%m/%d/%Y"))
            return df_in

        merged = fmt(merged)
        hourly = fmt(hourly)

        desired_cols = ["date", "time on", "time off", "hvac temp set", "status"]
        merged = merged.reindex(columns=desired_cols)
        hourly = hourly.reindex(columns=desired_cols)

        uname = str(username)[:27]
        sheets[uname] = merged if not merged.empty else pd.DataFrame(
            [{"date": "", "time on": "", "time off": "", "hvac temp set": "", "status": ""}]
        )
        sheets[f"{uname}_hourly"] = hourly if not hourly.empty else pd.DataFrame(
            [{"date": "", "time on": "", "time off": "", "hvac temp set": "", "status": ""}]
        )

    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        for s, df_s in sheets.items():
            df_s.to_excel(writer, sheet_name=s, index=False)

    return {"excel": output_excel, "sheets": list(sheets.keys())}


# ============================
#  MAIN
# ============================
if __name__ == "__main__":
    res = generate_schedules()
    print("Done. Results:", res)
