"""
Generate synthetic HVAC-weather dataset, label with 4-hour lookahead "oracle" policy,
train a RandomForest classifier, and save CSV + trained model.

Outputs (saved under `model/`):
- `data/hvac_synthetic_dataset.csv`
- `models/hvac_on_model_rf.joblib`

Run:
    python model/generate_train.py

Requires: numpy, pandas, scikit-learn, joblib
"""
import os
from pathlib import Path
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, confusion_matrix

# Import helper functions from model.module
from model.model import (
    calculate_zone_heat_balance_RC,
    calculate_cop_cooling,
    calculate_cop_heating,
    calculate_mpc_cost,
)

OUT_DIR = Path(__file__).resolve().parent
DATA_DIR = OUT_DIR / "data"
MODEL_DIR = OUT_DIR / "models"
DATA_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

np.random.seed(42)

# Parameters
DAYS = 90
HOURS = DAYS * 24
DESIRED_TEMP = 22.0
HOME_SIZE_M2 = 120.0

# Simple synthetic weather generator
def synth_weather(start: datetime, hours: int):
    ts = [start + timedelta(hours=i) for i in range(hours)]
    # Base temperature: daily sinusoid + seasonal trend
    day_of_year = np.array([t.timetuple().tm_yday for t in ts])
    hour_of_day = np.array([t.hour for t in ts])

    seasonal = 8.0 * np.sin((day_of_year / 365.0) * 2 * np.pi)  # +/-8°C seasonal
    daily = 6.0 * np.sin((hour_of_day - 4) / 24.0 * 2 * np.pi)  # daily swing
    noise = np.random.normal(0, 1.2, size=hours)

    temp = 15.0 + seasonal + daily + noise  # around 15°C average
    humidity = np.clip(55 + 10*np.sin((hour_of_day)/24.0*2*np.pi) + np.random.normal(0,5,hours), 15, 95)
    solar = np.clip(400 * np.maximum(0, np.sin((hour_of_day - 6)/24.0*2*np.pi)) + np.random.normal(0,30,hours), 0, 1000)
    wind = np.clip(1.5 + np.random.exponential(1.0, hours), 0, 10)

    df = pd.DataFrame({
        "datetime": ts,
        "temp": temp,
        "humidity": humidity,
        "solar": solar,
        "wind": wind,
    })
    return df

# Simple labeling oracle: compare cumulative cost over 4-hour horizon
HORIZON = 4

# Helper simulate horizon: option hvac_on_bool = True means HVAC ON for all horizon steps
# We'll use calculate_zone_heat_balance_RC to update indoor temperature
def simulate_horizon(T_start, weather_slice, hvac_on_bool, desired_temp=DESIRED_TEMP,
                      C_thermal=0.5e6, R_total=0.01, home_size=HOME_SIZE_M2,
                      hvac_age=5, hvac_type="furnace"):
    T_zone = T_start
    total_energy = 0.0
    total_comfort_penalty = 0.0

    for i in range(len(weather_slice)):
        row = weather_slice.iloc[i]
        Tout = float(row["temp"])
        solar = float(row["solar"])
        humidity = float(row["humidity"])
        wind = float(row["wind"])

        # Rough HVAC load: proportional to temperature gap and home size
        if hvac_on_bool:
            # Bring towards desired: we compute Q_hvac positive for heating when desired > T_zone
            Q_hvac = max(0.0, (desired_temp - T_zone) * home_size * 40.0)  # W
            # If T_zone > desired (overheated) consider cooling (positive Q for cooling energy)
            if T_zone > desired_temp + 0.5:
                Q_hvac = max(0.0, (T_zone - desired_temp) * home_size * 40.0)
        else:
            Q_hvac = 0.0

        # Choose COP
        if T_zone < desired_temp:
            COP = calculate_cop_heating(Tout, desired_temp, part_load_ratio=0.8, hvac_age=hvac_age, is_heatpump=(hvac_type=="heat_pump"))
        else:
            COP = calculate_cop_cooling(Tout, desired_temp, part_load_ratio=0.8, hvac_age=hvac_age)

        # Energy in kWh for this hour
        energy_kwh = Q_hvac / (COP * 1000.0) if COP > 0 else 0.0
        total_energy += energy_kwh

        # Comfort penalty: absolute deviation (°C)
        comfort_penalty = abs(T_zone - desired_temp)
        total_comfort_penalty += comfort_penalty

        # Update indoor temp with RC model (use same R/C scaling as in model)
        T_zone = calculate_zone_heat_balance_RC(
            T_zone_prev=T_zone,
            T_outdoor=Tout,
            T_radiant=Tout*0.5 + T_zone*0.5,
            Q_internal=200.0, Q_solar=solar*0.05, Q_hvac=(Q_hvac if hvac_on_bool else 0.0),
            Q_inf=0.0, dt_hour=1.0, C_thermal=C_thermal, R_total=R_total
        )

    # Compose cost: weighted sum (energy + comfort)
    alpha = 0.7
    beta = 0.3
    cost = alpha * total_energy + beta * total_comfort_penalty
    return cost, total_energy, total_comfort_penalty


def build_dataset(weather_df):
    rows = []
    indoor_T = DESIRED_TEMP  # start indoor at desired
    C_thermal = 0.5e6
    R_total = 0.01

    for t in range(len(weather_df) - HORIZON):
        now = weather_df.iloc[t]
        # features
        temp_now = now["temp"]
        hum_now = now["humidity"]
        solar_now = now["solar"]
        wind_now = now["wind"]
        hour = now["datetime"].hour

        # occupancy simple schedule: home during morning/night
        occupancy = 1 if (hour >= 6 and hour < 22) else 0

        # forecasts (1-4h)
        temps_f = [weather_df.iloc[t + k]["temp"] for k in range(1, HORIZON + 1)]
        solar_f = [weather_df.iloc[t + k]["solar"] for k in range(1, HORIZON + 1)]

        # Label via oracle: compare HVAC ON for next 4h vs OFF for next 4h
        weather_slice = weather_df.iloc[t + 1: t + 1 + HORIZON].reset_index(drop=True)

        cost_on, e_on, c_on = simulate_horizon(indoor_T, weather_slice, True,
                                              DESIRED_TEMP, C_thermal, R_total)
        cost_off, e_off, c_off = simulate_horizon(indoor_T, weather_slice, False,
                                                 DESIRED_TEMP, C_thermal, R_total)
        label = 1 if cost_on < cost_off else 0

        row = {
            "datetime": now["datetime"],
            "temp": temp_now,
            "hum": hum_now,
            "solar": solar_now,
            "wind": wind_now,
            "occupancy": occupancy,
            "indoor_temp": indoor_T,
            "temp_f1": temps_f[0],
            "temp_f2": temps_f[1],
            "temp_f3": temps_f[2],
            "temp_f4": temps_f[3],
            "solar_f1": solar_f[0],
            "label_turn_on_now": label,
        }
        rows.append(row)

        # Update indoor temperature one step realistically using no-HVAC (assume HVAC action unknown)
        indoor_T = calculate_zone_heat_balance_RC(
            T_zone_prev=indoor_T,
            T_outdoor=temp_now,
            T_radiant=temp_now*0.5 + indoor_T*0.5,
            Q_internal=200.0, Q_solar=solar_now*0.05, Q_hvac=0.0,
            Q_inf=0.0, dt_hour=1.0, C_thermal=C_thermal, R_total=R_total
        )

    df = pd.DataFrame(rows)
    return df


def train_model(df):
    features = [
        "temp", "temp_f1", "temp_f2", "temp_f3", "temp_f4",
        "solar", "hum", "wind", "occupancy", "indoor_temp",
    ]
    X = df[features].values
    y = df["label_turn_on_now"].values

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    clf = RandomForestClassifier(n_estimators=200, random_state=42, n_jobs=-1)
    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    cm = confusion_matrix(y_test, y_pred)

    print("Trained RandomForest: accuracy=", acc)
    print("Confusion matrix:\n", cm)

    # Feature importances
    importances = clf.feature_importances_
    for f, imp in zip(features, importances):
        print(f, imp)

    return clf, acc, cm


def main():
    start = datetime(2025, 1, 1, 0, 0)
    weather = synth_weather(start, HOURS + HORIZON + 10)
    df = build_dataset(weather)

    csv_path = DATA_DIR / "hvac_synthetic_dataset.csv"
    df.to_csv(csv_path, index=False)
    print("Saved dataset to:", csv_path)

    clf, acc, cm = train_model(df)
    model_path = MODEL_DIR / "hvac_on_model_rf.joblib"
    joblib.dump(clf, model_path)
    print("Saved model to:", model_path)

if __name__ == '__main__':
    main()
