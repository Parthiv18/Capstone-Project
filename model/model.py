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
    db_path = base / "weather-app" / "backend" / "database" / "users.db"
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
    d = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if v.isdigit():
            v = int(v)
        d[k] = v
    return d


# ============================
#  WEATHER PARSER
# ============================
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def parse_weather(text: str) -> pd.DataFrame:
    if not text:
        return pd.DataFrame(columns=["date", "temp", "humidity", "apparent", "solar"])

    try:
        obj = json.loads(text)

        rows = None
        if isinstance(obj, dict) and "rows" in obj:
            rows = obj["rows"]
        elif isinstance(obj, list):
            rows = obj

        if rows is not None:
            parsed = []
            for r in rows:
                dt_val = r.get("date")
                if not dt_val:
                    continue

                try:
                    dt_parsed = datetime.fromisoformat(dt_val.replace(" EST", ""))
                except:
                    continue

                parsed.append({
                    "date": dt_parsed,
                    "temp": float(r.get("temperature_2m", 0)),
                    "humidity": float(r.get("humidity_2m", 50)),
                    "apparent": float(r.get("apparent_temperature", r.get("temperature_2m", 0))),
                    "solar": float(r.get("solar_radiation", 0)),
                })

            return pd.DataFrame(parsed)

    except:
        pass

    return pd.DataFrame(columns=["date", "temp", "humidity", "apparent", "solar"])


# ============================
#  PREDICTIVE HVAC MODEL
# ============================
def compute_hvac_intervals(df: pd.DataFrame, house_vars: dict) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            "date", "time_start", "time_end",
            "mode", "hvac_temp_set", "hvac_status"
        ])

    df = df.sort_values("date").reset_index(drop=True)

    # Base comfort temperature
    desired = house_vars.get("personal_comfort", 22)
    try:
        desired = float(desired)
    except:
        desired = 22.0

    # Insulation map → how fast house loses heat
    insulation_map = {
        "poor": 1.6,
        "average": 1.0,
        "good": 0.7,
        "excellent": 0.4
    }
    insulation_factor = insulation_map.get(
        str(house_vars.get("insulation_quality", "average")).lower(),
        1.0
    )

    states = []
    hvac_setpoints = []
    hvac_statuses = []

    current_mode = "off"

    # Helper to safely get future temps
    def safe_temp(idx):
        return df.loc[idx, "temp"] if idx < len(df) else df.loc[len(df) - 1, "temp"]

    for i in range(len(df)):
        row = df.loc[i]

        t = row["temp"]
        humidity = row["humidity"]
        apparent = row["apparent"]
        solar = row["solar"]

        if t is None:
            states.append("off")
            hvac_setpoints.append("")
            hvac_statuses.append("off")
            current_mode = "off"
            continue

        # -----------------------------------------
        # 1) Effective Current Temp (feels-like)
        # -----------------------------------------
        effective_temp = (
            0.6 * t +
            0.3 * apparent +
            0.1 * (t - (humidity / 10))
        )
        if solar >= 150:
            effective_temp += 0.2  # free solar warming

        # -----------------------------------------
        # 2) Predictive Trend (1h, 3h, 6h, 12h forecast)
        # -----------------------------------------
        T_now = t
        T_1h = safe_temp(i+1)
        T_3h = safe_temp(i+3)
        T_6h = safe_temp(i+6)
        T_12h = safe_temp(i+12)

        trend = (
            0.4 * (T_1h - T_now) +
            0.3 * (T_3h - T_now) +
            0.2 * (T_6h - T_now) +
            0.1 * (T_12h - T_now)
        )

        predictive_temp = effective_temp + (trend * insulation_factor)

        # -----------------------------------------
        # 3) Time-based Comfort Mode (Option C)
        # -----------------------------------------
        hour = row["date"].hour

        if 6 <= hour < 10:
            comfort_band = 0.5       # strict (morning)
        elif 10 <= hour < 22:
            comfort_band = 1.0       # normal (day)
        else:
            comfort_band = 2.0       # energy saver (night)

        delta = comfort_band
        hysteresis = comfort_band / 2

        # -----------------------------------------
        # 4) Determine MODE using predictive_temp
        # -----------------------------------------
        if current_mode == "heating":
            if predictive_temp >= desired - (delta - hysteresis):
                current_mode = "off"

        elif current_mode == "cooling":
            if predictive_temp <= desired + (delta - hysteresis):
                current_mode = "off"

        else:
            if predictive_temp <= desired - delta:
                current_mode = "heating"
            elif predictive_temp >= desired + delta:
                current_mode = "cooling"
            else:
                current_mode = "off"

        # -----------------------------------------
        # 5) HVAC Temp Setpoint (based on humidity)
        # -----------------------------------------
        if current_mode == "heating":
            humidity_adj = -0.2 if humidity > 80 else 0
            setpoint = desired - 0.3 + humidity_adj

        elif current_mode == "cooling":
            humidity_adj = +0.3 if humidity > 70 else 0
            setpoint = desired + 0.5 + humidity_adj

        else:
            setpoint = ""

        # -----------------------------------------
        # 6) Predictive HVAC ON/OFF Status
        # -----------------------------------------
        if current_mode == "off":
            hvac_status = "off"
        else:
            diff = abs(predictive_temp - desired)

            if diff < comfort_band * 0.5:
                hvac_status = "off"  # close enough → save money
            else:
                hvac_status = "on"

        states.append(current_mode)
        hvac_setpoints.append(setpoint)
        hvac_statuses.append(hvac_status)

    # -----------------------------------------
    # 7) Build Schedule DataFrame
    # -----------------------------------------
    intervals = []
    for i in range(len(df)):
        dt = df.loc[i, "date"]
        intervals.append({
            "date": dt.date(),
            "time_start": dt.strftime("%H:%M"),
            "time_end": (dt + timedelta(hours=1)).strftime("%H:%M"),
            "mode": states[i],
            "hvac_temp_set": hvac_setpoints[i],
            "hvac_status": hvac_statuses[i],
        })

    return pd.DataFrame(intervals)


# ============================
#  EXPORT TO EXCEL
# ============================
def generate_schedules(output_excel: str = "hvac_schedules.xlsx"):
    db_path = get_db_path()
    rows = fetch_all_users(db_path)

    sheets = {}

    for username, user_weather, user_house in rows:
        house_vars = parse_house(user_house or "")
        df_weather = parse_weather(user_weather or "")

        intervals = compute_hvac_intervals(df_weather, house_vars)

        if intervals.empty:
            intervals = pd.DataFrame([{
                "Date": "", "time start": "", "time off": "",
                "mode": "none", "hvac temp set": "", "hvac status": ""
            }])
        else:
            intervals["Date"] = intervals["date"].apply(lambda d: d.strftime("%m/%d/%Y"))
            intervals = intervals[[
                "Date", "time_start", "time_end",
                "mode", "hvac_temp_set", "hvac_status"
            ]]
            intervals.columns = [
                "Date", "time start", "time off",
                "mode", "hvac temp set", "hvac status"
            ]

        sheets[username] = intervals

    try:
        with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
            for username, df in sheets.items():
                df.to_excel(writer, sheet_name=str(username)[:31], index=False)
    except ModuleNotFoundError:
        print("Error: openpyxl not installed.")
        return None

    return {"excel": output_excel, "sheets": list(sheets.keys())}


# ============================
#  MAIN
# ============================
if __name__ == "__main__":
    res = generate_schedules()
    print("Done. Results:", res)
