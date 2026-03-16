import sqlite3
import pandas as pd
import json

def calculate_metrics(db_path):
    try:
        conn = sqlite3.connect(db_path)
        
        # This helper finds exactly what columns you have
        cursor = conn.execute("SELECT * FROM user_thermostat LIMIT 1")
        col_names = [description[0] for description in cursor.description]
        
        # Map your report variables to what's likely in the DB
        # Based on your screenshots, 'target_se...' is likely 'target_setpoint'
        target_col = next((c for c in col_names if 'target' in c), None)
        
        query = f"SELECT sim_inside_temp, hvac_sim, {target_col}, last_updated FROM user_thermostat"
        df = pd.read_sql_query(query, conn)
        
        print(f"--- {len(df)} Simulation Runs Tracked ---")
        
        for i, row in df.iterrows():
            sim_data = json.loads(row['hvac_sim'])
            actions = sim_data.get('actions', [])
            
            # Analyze Heuristic Algorithm Results [cite: 145, 146]
            heat_hours = sum(1 for a in actions if a['mode'] == 'heat')
            cool_hours = sum(1 for a in actions if a['mode'] == 'cool')
            pre_active = any('pre' in a['mode'] for a in actions)
            
            print(f"\nRun {i+1} | Date: {row['last_updated']}")
            print(f"  - Starting T_in: {row['sim_inside_temp']}°C [cite: 85]")
            print(f"  - Target:        {row[target_col]}°C [cite: 366]")
            print(f"  - HVAC Load:     {heat_hours}h Heat / {cool_hours}h Cool")
            
            if pre_active:
                print("  - Optimization:  Load shifting detected! (Pre-heating/cooling active) [cite: 92]")

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    db_path = r'C:\\Users\\Parthiv\\Downloads\\VS Code\\School Related\\Capstone Project\\weather-app\\database\\users.db'
    calculate_metrics(db_path)