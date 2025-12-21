"""
Database Module
SQLite database operations for user management and data storage.
"""

from pathlib import Path
from contextlib import contextmanager
import sqlite3
import hashlib
import secrets
import json
from typing import Any, Optional

# ============================================================
# Configuration
# ============================================================

DB_PATH = Path(__file__).parent / "users.db"
PBKDF2_ITERATIONS = 200_000

# ============================================================
# Database Connection Management
# ============================================================

@contextmanager
def get_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema():
    """Create database schema if it doesn't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    with get_connection() as conn:
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                address TEXT,
                user_weather TEXT,
                user_house TEXT,
                weather_date TEXT
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_thermostat (
                user_id INTEGER PRIMARY KEY,
                sim_inside_temp REAL NOT NULL,
                hvac_sim TEXT,
                target_setpoint REAL DEFAULT 22.0,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        
        # Add target_setpoint column if it doesn't exist (migration for existing DBs)
        try:
            cur.execute("ALTER TABLE user_thermostat ADD COLUMN target_setpoint REAL DEFAULT 22.0")
        except:
            pass  # Column already exists


# Initialize schema on module import
_ensure_schema()

# ============================================================
# Password Utilities
# ============================================================

def _hash_password(password: str, salt: bytes = None) -> tuple[str, str]:
    """Hash a password using PBKDF2-SHA256."""
    if salt is None:
        salt = secrets.token_bytes(16)
    
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS
    )
    return hashed.hex(), salt.hex()


# ============================================================
# JSON Utilities
# ============================================================

def _to_json(data: Any) -> str:
    """Convert data to JSON string."""
    if isinstance(data, (dict, list)):
        return json.dumps(data)
    try:
        return json.dumps(json.loads(data))
    except Exception:
        return json.dumps({"text": str(data)})


def _from_json(raw: str) -> Any:
    """Parse JSON string to Python object."""
    try:
        return json.loads(raw)
    except Exception:
        return raw


# ============================================================
# User Management
# ============================================================

def create_user(username: str, password: str, address: str = None) -> bool:
    """Create a new user. Returns False if username exists."""
    with get_connection() as conn:
        cur = conn.cursor()
        
        # Check if user exists
        cur.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        if cur.fetchone():
            return False
        
        pw_hash, salt = _hash_password(password)
        cur.execute(
            "INSERT INTO users (username, password_hash, salt, address) VALUES (?, ?, ?, ?)",
            (username, pw_hash, salt, address)
        )
        return True


def verify_user(username: str, password: str) -> bool:
    """Verify user credentials."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT password_hash, salt FROM users WHERE username = ?",
            (username,)
        )
        row = cur.fetchone()
        
        if not row:
            return False
        
        stored_hash = row[0]
        salt = bytes.fromhex(row[1])
        computed_hash, _ = _hash_password(password, salt=salt)
        
        return secrets.compare_digest(computed_hash, stored_hash)


def get_user_id(username: str) -> Optional[int]:
    """Get user ID by username."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        return row[0] if row else None


def get_user_address(username: str) -> Optional[str]:
    """Get user's address."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT address FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        return row[0] if row else None


# ============================================================
# Weather Data
# ============================================================

def set_user_weather(username: str, data: Any) -> bool:
    """Save weather data for a user."""
    payload = _to_json(data)
    
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET user_weather = ? WHERE username = ?",
            (payload, username)
        )
        return cur.rowcount > 0


def get_user_weather(username: str) -> Optional[Any]:
    """Get user's weather data."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_weather FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        
        if not row or row[0] is None:
            return None
        
        return _from_json(row[0])


def set_user_weather_with_date(username: str, data: Any, weather_date: str) -> bool:
    """Save weather data with date tracking."""
    with get_connection() as conn:
        cur = conn.cursor()
        
        # Get existing weather data
        cur.execute("SELECT user_weather FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        existing = row[0] if row else None
        
        # Parse existing snapshots
        snapshots = []
        if existing:
            try:
                parsed = json.loads(existing)
                snapshots = parsed if isinstance(parsed, list) else [parsed]
            except Exception:
                snapshots = [{"text": existing}]
        
        # Add new snapshot
        entry_data = data if isinstance(data, (dict, list)) else _from_json(data)
        snapshots.append({"date": weather_date, "data": entry_data})
        
        cur.execute(
            "UPDATE users SET user_weather = ?, weather_date = ? WHERE username = ?",
            (json.dumps(snapshots), weather_date, username)
        )
        return cur.rowcount > 0


def get_user_weather_date(username: str) -> Optional[str]:
    """Get the date of user's last weather update."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT weather_date FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        return row[0] if row else None


# ============================================================
# House Data
# ============================================================

def set_user_house(username: str, data: Any) -> bool:
    """Save house variables for a user."""
    payload = _to_json(data)
    
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET user_house = ? WHERE username = ?",
            (payload, username)
        )
        return cur.rowcount > 0


def get_user_house(username: str) -> Optional[Any]:
    """Get user's house variables."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_house FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        
        if not row or row[0] is None:
            return None
        
        return _from_json(row[0])


# ============================================================
# Thermostat/Simulation Data
# ============================================================

def get_simulated_temp(user_id: int) -> Optional[float]:
    """Get simulated indoor temperature for a user."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT sim_inside_temp FROM user_thermostat WHERE user_id = ?",
            (user_id,)
        )
        row = cur.fetchone()
        return row["sim_inside_temp"] if row else None


def get_last_updated(user_id: int) -> Optional[str]:
    """Get the last update timestamp for a user's simulation."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT last_updated FROM user_thermostat WHERE user_id = ?",
            (user_id,)
        )
        row = cur.fetchone()
        return row["last_updated"] if row else None


def update_simulated_temp(user_id: int, new_temp: float) -> None:
    """Update or insert simulated indoor temperature."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_thermostat (user_id, sim_inside_temp, last_updated)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                sim_inside_temp = excluded.sim_inside_temp,
                last_updated = CURRENT_TIMESTAMP
        """, (user_id, new_temp))


def set_hvac_sim(user_id: int, hvac_sim: Any) -> bool:
    """Store HVAC simulation data."""
    payload = _to_json(hvac_sim)
    
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE user_thermostat SET hvac_sim = ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?",
            (payload, user_id)
        )
        return cur.rowcount > 0


def get_hvac_sim(user_id: int) -> Optional[Any]:
    """Get HVAC simulation data."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT hvac_sim FROM user_thermostat WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        
        if not row or row[0] is None:
            return None
        
        return _from_json(row[0])


def get_target_setpoint(user_id: int) -> Optional[float]:
    """Get user's target temperature setpoint."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT target_setpoint FROM user_thermostat WHERE user_id = ?",
            (user_id,)
        )
        row = cur.fetchone()
        return row["target_setpoint"] if row and row["target_setpoint"] is not None else None


def set_target_setpoint(user_id: int, setpoint: float) -> bool:
    """Set user's target temperature setpoint."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_thermostat (user_id, sim_inside_temp, target_setpoint, last_updated)
            VALUES (?, 22.0, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                target_setpoint = excluded.target_setpoint,
                last_updated = CURRENT_TIMESTAMP
        """, (user_id, setpoint))
        return True


# ============================================================
# Composite Queries
# ============================================================

def get_user_state(username: str) -> Optional[dict]:
    """Get complete user state for simulation."""
    user_id = get_user_id(username)
    if user_id is None:
        return None

    return {
        "id": user_id,
        "address": get_user_address(username),
        "house": get_user_house(username),
        "weather": get_user_weather(username),
        "weather_date": get_user_weather_date(username),
        "simulated_temp": get_simulated_temp(user_id),
        "last_updated": get_last_updated(user_id),
        "hvac_sim": get_hvac_sim(user_id),
        "target_setpoint": get_target_setpoint(user_id),
    }


# Aliases for backward compatibility
save_user_weather = set_user_weather
save_user_house = set_user_house
