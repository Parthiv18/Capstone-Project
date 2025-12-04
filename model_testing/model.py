import sqlite3
import json
import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta
from pathlib import Path

# --- CONFIGURATION ---
DB_PATH = r"C:\Users\Parthiv\Downloads\VS Code\School Related\Capstone Project\weather-app\database\users.db"

# Physics Constants
AIR_DENSITY = 1.225  # kg/m³
CP_AIR = 1005        # J/kg·K
DT_SECONDS = 60      # 1-minute simulation step

# Smart Control Constants
MIN_RUN_TIME = 15    # Minutes: Minimum time HVAC must stay ON
MIN_OFF_TIME = 10    # Minutes: Minimum time HVAC must stay OFF

class SmartHVACModel:
    def __init__(self, house_params):
        # 1. House Physics Setup
        self.area = float(house_params.get('home_size', 100)) 
        self.ceiling_height = 2.5 
        self.volume = self.area * self.ceiling_height
        
        # Randomized Setpoint (20-24°C)
        self.setpoint = float(random.randint(20, 24))
        self.hvac_power = 3500 # Watts
        
        quality = house_params.get('insulation_quality', 'average').lower()
        if quality == 'poor': self.u_val = 1.5
        elif quality == 'excellent': self.u_val = 0.2
        else: self.u_val = 0.5
            
        # Thermal Mass
        mass_air = self.volume * AIR_DENSITY
        self.c_home = (mass_air * CP_AIR) * 5 

    def calculate_next_temp(self, t_in, t_out, solar, hvac_mode):
        """Calculates T_in for the next minute."""
        q_cond = self.u_val * self.area * (t_out - t_in)
        q_solar = (self.area * 0.15) * 0.5 * solar
        
        q_hvac = 0
        # "COOL" matches both "COOL" and "PRE-COOL"
        if "COOL" in hvac_mode: q_hvac = -self.hvac_power
        # "HEAT" matches both "HEAT" and "PRE-HEAT"
        elif "HEAT" in hvac_mode: q_hvac = self.hvac_power
            
        q_total = q_cond + q_solar + q_hvac
        delta_t = (q_total * DT_SECONDS) / self.c_home
        return t_in + delta_t

def get_weather_1min_resolution(json_str):
    try:
        data = json.loads(json_str)
        if isinstance(data, list): data = data[-1]['data']
        if 'rows' not in data: return None

        df = pd.DataFrame(data['rows'])
        df['date'] = df['date'].str.replace(' EST', '')
        df['date'] = pd.to_datetime(df['date'])
        
        cols = ['temperature_2m', 'solar_radiation']
        for c in cols: df[c] = pd.to_numeric(df[c])

        df = df.set_index('date')
        df_1min = df.resample('1min').interpolate(method='linear')
        df_1min = df_1min.reset_index()
        return df_1min
    except Exception as e:
        print(f"Data error: {e}")
        return None

def generate_schedule():
    print(f"Connecting to DB at: {DB_PATH}")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT username, user_weather, user_house FROM users")
        users = cursor.fetchall()
    except Exception as e:
        print(f"DB Error: {e}")
        return

    excel_writer = pd.ExcelWriter("Smart_Home_HVAC_Schedule.xlsx", engine='openpyxl')
    
    for user in users:
        username = user['username']
        df = get_weather_1min_resolution(user['user_weather'])
        if df is None: continue
        
        try:
            house_params = json.loads(user['user_house'])
            if isinstance(house_params, list): house_params = house_params[0]
        except: continue
            
        model = SmartHVACModel(house_params)
        print(f"Simulating User: {username} | Target: {model.setpoint}°C")
        
        schedule_rows = []
        current_temp = model.setpoint
        current_mode = "OFF"
        
        # Block Aggregation
        block_start_time = df.iloc[0]['date']
        block_temps_in = []
        block_temps_out = []
        
        minutes_in_current_state = 0
        DEADBAND = 0.5 
        PREDICT_WINDOW = 60 # Look ahead 60 minutes
        
        for i in range(len(df)):
            row = df.iloc[i]
            t_out = row['temperature_2m']
            solar = row['solar_radiation']
            dt_now = row['date']
            
            new_mode = current_mode 
            
            # --- 1. AI PREDICTION ---
            pred_temp = current_temp
            look_ahead_limit = min(i + PREDICT_WINDOW, len(df))
            for k in range(i, look_ahead_limit, 5): 
                pred_row = df.iloc[k]
                pred_temp = model.calculate_next_temp(pred_temp, pred_row['temperature_2m'], pred_row['solar_radiation'], "OFF")
            
            # --- 2. LOGIC ---
            is_locked = False
            if current_mode != "OFF" and minutes_in_current_state < MIN_RUN_TIME:
                is_locked = True
            elif current_mode == "OFF" and minutes_in_current_state < MIN_OFF_TIME:
                is_locked = True
            
            if not is_locked:
                if current_mode == "OFF":
                    # Priority A: URGENT (Reactive)
                    if current_temp > model.setpoint + DEADBAND:
                        new_mode = "COOL"
                    elif current_temp < model.setpoint - DEADBAND:
                        new_mode = "HEAT"
                    
                    # Priority B: FUTURE (Predictive)
                    # Only start PRE-COOL if we are currently comfortable, but danger is coming
                    else:
                        if pred_temp > model.setpoint + DEADBAND + 0.5:
                            new_mode = "PRE-COOL"
                        elif pred_temp < model.setpoint - DEADBAND - 0.5:
                            new_mode = "PRE-HEAT"

                else:
                    # Stopping Logic
                    # If we hit the target, turn OFF
                    if "COOL" in current_mode and current_temp <= model.setpoint:
                        new_mode = "OFF"
                    elif "HEAT" in current_mode and current_temp >= model.setpoint:
                        new_mode = "OFF"

            # --- 3. PHYSICS UPDATE ---
            current_temp = model.calculate_next_temp(current_temp, t_out, solar, new_mode)
            
            # --- 4. AGGREGATION ---
            if new_mode != current_mode or i == len(df) - 1:
                avg_in = sum(block_temps_in) / len(block_temps_in) if block_temps_in else current_temp
                avg_out = sum(block_temps_out) / len(block_temps_out) if block_temps_out else t_out
                
                schedule_rows.append({
                    "date": block_start_time.strftime("%Y-%m-%d"),
                    "hvac_start_time": block_start_time.strftime("%H:%M"),
                    "hvac_stop_time": dt_now.strftime("%H:%M"),
                    "outside_temp": round(avg_out, 2),
                    "inside_temp": round(avg_in, 2),
                    "hvac_set_point": model.setpoint,
                    "hvac_mode": current_mode  # Will now show PRE-COOL/PRE-HEAT/COOL/HEAT/OFF
                })
                
                current_mode = new_mode
                block_start_time = dt_now
                block_temps_in = []
                block_temps_out = []
                minutes_in_current_state = 0
            
            block_temps_in.append(current_temp)
            block_temps_out.append(t_out)
            minutes_in_current_state += 1

        if schedule_rows:
            user_df = pd.DataFrame(schedule_rows)
            sheet_name = str(username)[:30]
            user_df.to_excel(excel_writer, sheet_name=sheet_name, index=False)

    excel_writer.close()
    conn.close()
    print("Done. Generated 'Smart_Home_HVAC_Schedule.xlsx' with AI Pre-Conditioning.")

if __name__ == "__main__":
    generate_schedule()