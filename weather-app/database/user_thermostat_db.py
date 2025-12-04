import sqlite3
from pathlib import Path

# Define path relative to this file (stores it in backend/database/)
DB_PATH = Path(__file__).parent / "user_thermostat.db"

def _get_conn():
    """Creates connection to the thermostat database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_thermostat_db():
    """Initializes the table if it doesn't exist."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_thermostat_db (
            username TEXT PRIMARY KEY,
            sim_inside_temp REAL NOT NULL,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_simulated_temp(username: str):
    """Fetches the current simulated temperature for a user."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT sim_inside_temp FROM user_thermostat_db WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    return row['sim_inside_temp'] if row else None

def update_simulated_temp(username: str, new_temp: float):
    """Updates or inserts the simulated temperature."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_thermostat_db (username, sim_inside_temp, last_updated)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(username) DO UPDATE SET
            sim_inside_temp = excluded.sim_inside_temp,
            last_updated = CURRENT_TIMESTAMP
    """, (username, new_temp))
    conn.commit()
    conn.close()

# Initialize immediately when module is imported
init_thermostat_db()