from pathlib import Path
import sqlite3
import json
import re
from datetime import datetime, timedelta
import pandas as pd


def get_db_path() -> Path:
    # users.db lives in the backend database folder of the weather-app
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


def parse_house(text: str) -> dict:
    if not text:
        return {}
    # text is saved as lines like 'key: value' (see house_api)
    d = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        # try to convert to int when appropriate
        if v.isdigit():
            v = int(v)
        d[k] = v
    return d


DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def parse_weather(text: str) -> pd.DataFrame:
    """Attempt to extract a time series of (datetime, temperature).

    The function supports a few formats: JSON (list/dict with rows), or plain text
    dumped from pandas (lines containing a date and numeric columns). It returns
    a DataFrame with columns `date` (timezone-naive datetime) and `temp` (float).
    """
    if not text:
        return pd.DataFrame(columns=["date", "temp"])

    # Try JSON first
    try:
        obj = json.loads(text)
        # Common format from the weather API: dict with 'rows' list
        rows = None
        if isinstance(obj, dict) and "rows" in obj and isinstance(obj["rows"], list):
            rows = obj["rows"]
        elif isinstance(obj, list):
            rows = obj
        if rows is not None:
            def _parse_dt(s: str):
                if s is None:
                    return None
                st = str(s)
                # try ISO first
                try:
                    return datetime.fromisoformat(st)
                except Exception:
                    pass
                # If there's a trailing timezone abbreviation (e.g. ' EST'), strip it and parse
                parts = st.rsplit(" ", 1)
                if len(parts) == 2 and parts[1].isalpha():
                    try:
                        return datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass
                # try with %Z (may or may not recognize abbreviation)
                try:
                    return datetime.strptime(st, "%Y-%m-%d %H:%M:%S %Z")
                except Exception:
                    pass
                # final fallback: try without timezone
                try:
                    return datetime.strptime(st, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    return None

            parsed = []
            for r in rows:
                # prefer keys that contain 'date'
                dt_val = None
                for k, v in r.items():
                    if "date" in str(k).lower():
                        dt_val = v
                        break
                # find a temperature-like key (e.g. 'temperature_2m')
                temp = None
                for k, v in r.items():
                    if "temp" in str(k).lower() or "temperature" in str(k).lower():
                        try:
                            temp = float(v)
                        except Exception:
                            temp = None
                        break
                if dt_val is None:
                    continue
                dt_parsed = _parse_dt(dt_val)
                if dt_parsed is None:
                    continue
                parsed.append({"date": dt_parsed, "temp": temp})
            return pd.DataFrame(parsed)
    except Exception:
        pass

    # Fallback: parse plain text lines for a date and a following numeric (temperature)
    parsed = []
    for line in text.splitlines():
        m = DATE_RE.search(line)
        if not m:
            continue
        date_str = m.group(1)
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        # find the first number after the date in the line
        after = line[m.end():]
        num_match = re.search(r"(-?\d+\.?\d*)", after)
        temp = float(num_match.group(1)) if num_match else None
        parsed.append({"date": dt, "temp": temp})

    return pd.DataFrame(parsed)


def compute_hvac_intervals(df: pd.DataFrame, house_vars: dict) -> pd.DataFrame:
    """Given weather timeseries (date,temp) and house variables, produce HVAC on/off intervals.

    Basic beginner strategy:
      - desired temperature from `personal_comfort` (default 22)
      - occupancy setback: if occupancy contains 'away' widen deadband
      - simple hysteresis to avoid chattering
      - heating when outside temp <= desired - delta
      - cooling when outside temp >= desired + delta
    """
    if df.empty:
        return pd.DataFrame(columns=["username", "mode", "start", "end", "duration_hours", "avg_temp"])

    df = df.sort_values("date").reset_index(drop=True)

    desired = house_vars.get("personal_comfort") if house_vars.get("personal_comfort") is not None else 22
    try:
        desired = float(desired)
    except Exception:
        desired = 22.0

    occupancy = str(house_vars.get("occupancy", "")).lower()
    setback = 3.0 if any(k in occupancy for k in ("away", "vacant", "out")) else 1.0
    delta = setback
    hysteresis = 0.5

    states = []  # 'off', 'heating', 'cooling'
    current = "off"
    for _, row in df.iterrows():
        t = row.get("temp")
        if t is None:
            states.append("off")
            current = "off"
            continue
        # decide based on current state (hysteresis)
        if current == "heating":
            if t >= desired - (delta - hysteresis):
                current = "off"
        elif current == "cooling":
            if t <= desired + (delta - hysteresis):
                current = "off"
        else:
            if t <= desired - delta:
                current = "heating"
            elif t >= desired + delta:
                current = "cooling"
            else:
                current = "off"
        states.append(current)

    df = df.copy()
    df["state"] = states

    # determine interval grouping
    intervals = []
    start_idx = None
    for i, row in df.iterrows():
        s = row["state"]
        if s != "off" and start_idx is None:
            start_idx = i
        if start_idx is not None:
            # if current is off or last row, close interval
            end_condition = (s == "off") or (i == len(df) - 1)
            if end_condition:
                # end is current row if s==off -> previous row end, else current row
                if s == "off":
                    end_idx = i - 1
                else:
                    end_idx = i
                start_row = df.loc[start_idx]
                end_row = df.loc[end_idx]
                mode = df.loc[start_idx, "state"]
                start_time = start_row["date"]
                # estimate end time as end_row.date + interval
                if end_idx + 1 < len(df):
                    interval = df.loc[end_idx + 1, "date"] - end_row["date"]
                else:
                    # fallback to a 1-hour interval
                    interval = timedelta(hours=1)
                end_time = end_row["date"] + interval
                duration = (end_time - start_time).total_seconds() / 3600.0
                avg_temp = df.loc[start_idx : end_idx, "temp"].dropna().mean()
                intervals.append(
                    {
                        "mode": mode,
                        "start": start_time,
                        "end": end_time,
                        "duration_hours": round(duration, 3),
                        "avg_temp": None if pd.isna(avg_temp) else round(float(avg_temp), 2),
                    }
                )
                start_idx = None

    return pd.DataFrame(intervals)


def generate_schedules(output_excel: str = "hvac_schedules.xlsx", output_csv_dir: str = "hvac_csvs"):
    db_path = get_db_path()
    rows = fetch_all_users(db_path)
    out_dir = Path(output_csv_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sheets = {}
    for username, user_weather, user_house in rows:
        house_vars = parse_house(user_house or "")
        df_weather = parse_weather(user_weather or "")
        intervals = compute_hvac_intervals(df_weather, house_vars)
        if intervals.empty:
            # write an empty sheet note
            intervals = pd.DataFrame(
                [
                    {
                        "mode": "none",
                        "start": None,
                        "end": None,
                        "duration_hours": 0,
                        "avg_temp": None,
                    }
                ]
            )
        intervals["username"] = username
        # reorder
        intervals = intervals[["username", "mode", "start", "end", "duration_hours", "avg_temp"]]
        sheets[username] = intervals
        # CSV per user
        csv_path = out_dir / f"{username}_hvac_schedule.csv"
        intervals.to_csv(csv_path, index=False)

    # Try to save an Excel workbook with a sheet per user.
    try:
        with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
            for username, df in sheets.items():
                sheet_name = str(username)[:31]
                # convert datetimes to ISO strings so Excel shows them clearly
                df_to_write = df.copy()
                df_to_write["start"] = df_to_write["start"].apply(lambda d: d.isoformat() if d is not None else "")
                df_to_write["end"] = df_to_write["end"].apply(lambda d: d.isoformat() if d is not None else "")
                df_to_write.to_excel(writer, sheet_name=sheet_name, index=False)
    except ModuleNotFoundError:
        print("openpyxl not available; skipping Excel output. CSV files were created in:", out_dir)

    return {
        "excel": output_excel if Path(output_excel).exists() else None,
        "csv_dir": str(out_dir.resolve()),
        "sheets": list(sheets.keys()),
    }


if __name__ == "__main__":
    res = generate_schedules()
    print("Done. Results:", res)
