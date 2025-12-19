from pathlib import Path
import sqlite3
import hashlib
import secrets
import json


DB_PATH = Path(__file__).parent / "users.db"


def _ensure_db():
    """Create a fresh schema.

    This module is intended for a new DB file (delete the old users.db first).
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
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
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_thermostat (
            user_id INTEGER PRIMARY KEY,
            sim_inside_temp REAL NOT NULL,
            hvac_sim TEXT,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.commit()
    conn.close()


def _get_conn():
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_bytes(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return hashed.hex(), salt.hex()


def create_user(username: str, password: str, address: str | None = None) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE username = ?", (username,))
    if cur.fetchone():
        conn.close()
        return False

    pw_hash, salt = _hash_password(password)
    cur.execute(
        "INSERT INTO users (username, password_hash, salt, address) VALUES (?, ?, ?, ?)",
        (username, pw_hash, salt, address),
    )
    conn.commit()
    conn.close()
    return True


def verify_user(username: str, password: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT password_hash, salt FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False

    stored_hash = row[0]
    salt = bytes.fromhex(row[1])
    computed_hash, _ = _hash_password(password, salt=salt)
    return secrets.compare_digest(computed_hash, stored_hash)


def get_user_id(username: str) -> int | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def get_user_address(username: str) -> str | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT address FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def set_user_weather(username: str, text: str) -> bool:
    if isinstance(text, (dict, list)):
        payload = json.dumps(text)
    else:
        try:
            payload = json.dumps(json.loads(text))
        except Exception:
            payload = json.dumps({"text": str(text)})

    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET user_weather = ? WHERE username = ?", (payload, username))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return bool(changed)


def get_user_weather(username: str):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_weather FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] is None:
        return None

    raw = row[0]
    try:
        return json.loads(raw)
    except Exception:
        return raw


def set_user_house(username: str, text: str) -> bool:
    if isinstance(text, (dict, list)):
        payload = json.dumps(text)
    else:
        try:
            payload = json.dumps(json.loads(text))
        except Exception:
            payload = json.dumps({"text": str(text)})

    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET user_house = ? WHERE username = ?", (payload, username))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return bool(changed)


def get_user_house(username: str):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_house FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] is None:
        return None

    raw = row[0]
    try:
        return json.loads(raw)
    except Exception:
        return raw


def set_user_weather_with_date(username: str, data, weather_date: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("SELECT user_weather FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    existing = row[0] if row else None

    snapshots = []
    if existing:
        try:
            parsed = json.loads(existing)
            snapshots = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            snapshots = [{"text": existing}]

    if isinstance(data, (dict, list)):
        entry_data = data
    else:
        try:
            entry_data = json.loads(data)
        except Exception:
            entry_data = {"text": str(data)}

    snapshots.append({"date": weather_date, "data": entry_data})

    cur.execute(
        "UPDATE users SET user_weather = ?, weather_date = ? WHERE username = ?",
        (json.dumps(snapshots), weather_date, username),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return bool(changed)


def get_user_weather_date(username: str) -> str | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT weather_date FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def get_simulated_temp(user_id: int):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT sim_inside_temp FROM user_thermostat WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row["sim_inside_temp"] if row else None


def get_last_updated(user_id: int):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT last_updated FROM user_thermostat WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row["last_updated"] if row else None


def update_simulated_temp(user_id: int, new_temp: float):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_thermostat (user_id, sim_inside_temp, last_updated)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            sim_inside_temp = excluded.sim_inside_temp,
            last_updated = CURRENT_TIMESTAMP
        """,
        (user_id, new_temp),
    )
    conn.commit()
    conn.close()


def set_hvac_sim(user_id: int, hvac_sim) -> bool:
    """Store hvac sim data in user_thermostat.hvac_sim as JSON.

    Expected form:
    {"hvacData": {"hvac_mode": <text>, "hvac_time_on": <date>, "hvac_time_off": <date>, "set_to_overrride": <int>}}

    Accepts dict/list or JSON string.
    """
    if isinstance(hvac_sim, (dict, list)):
        payload = json.dumps(hvac_sim)
    else:
        try:
            payload = json.dumps(json.loads(hvac_sim))
        except Exception:
            payload = json.dumps({"text": str(hvac_sim)})

    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE user_thermostat SET hvac_sim = ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?",
        (payload, user_id),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return bool(changed)


def get_hvac_sim(user_id: int):
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT hvac_sim FROM user_thermostat WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] is None:
        return None

    raw = row[0]
    try:
        return json.loads(raw)
    except Exception:
        return raw


def get_user_state(username: str):
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
    }


def save_user_weather(username: str, data) -> bool:
    return set_user_weather(username, data)


def save_user_house(username: str, data) -> bool:
    return set_user_house(username, data)
