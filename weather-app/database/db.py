from pathlib import Path
import sqlite3
import hashlib
import secrets
import json


DB_PATH = Path(__file__).parent / "users.db"


def _ensure_db():
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
            postalcode TEXT,
            user_weather TEXT,
            user_house TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_thermostat (
            user_id INTEGER PRIMARY KEY,
            sim_inside_temp REAL NOT NULL,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    # Ensure columns exist for older DBs
    cur.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in cur.fetchall()}  # name is at index 1
    if "user_weather" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN user_weather TEXT")
    if "user_house" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN user_house TEXT")
    if "weather_date" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN weather_date TEXT")
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


def create_user(username: str, password: str, postalcode: str | None = None) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE username = ?", (username,))
    if cur.fetchone():
        conn.close()
        return False
    pw_hash, salt = _hash_password(password)
    cur.execute(
        "INSERT INTO users (username, password_hash, salt, postalcode) VALUES (?, ?, ?, ?)",
        (username, pw_hash, salt, postalcode),
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
    if not row:
        return None
    return row[0]


def get_user_postal(username: str) -> str | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT postalcode FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return row[0]


def set_user_weather(username: str, text: str) -> bool:
    # Accept a Python object (dict/list) or a string. Store as JSON string.
    if isinstance(text, (dict, list)):
        payload = json.dumps(text)
    else:
        # try to interpret string as JSON, otherwise wrap as text
        try:
            parsed = json.loads(text)
            payload = json.dumps(parsed)
        except Exception:
            payload = json.dumps({"text": str(text)})

    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET user_weather = ? WHERE username = ?", (payload, username))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return bool(changed)


def get_user_weather(username: str) -> str | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_weather FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    raw = row[0]
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def set_user_house(username: str, text: str) -> bool:
    # Store house variables as JSON. Accept dict/list or plain string.
    if isinstance(text, (dict, list)):
        payload = json.dumps(text)
    else:
        try:
            parsed = json.loads(text)
            payload = json.dumps(parsed)
        except Exception:
            # fallback: store as text field
            payload = json.dumps({"text": str(text)})

    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET user_house = ? WHERE username = ?", (payload, username))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return bool(changed)


def get_user_house(username: str) -> str | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_house FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    raw = row[0]
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def set_user_weather_with_date(username: str, data, weather_date: str) -> bool:
    """Append a weather snapshot to the user's `user_weather` JSON array and set `weather_date`.

    `data` may be a dict/list or a string. Stored format in `user_weather` will be a JSON
    array of objects with keys `date` and `data`.
    """
    conn = _get_conn()
    cur = conn.cursor()

    # Load existing weather
    cur.execute("SELECT user_weather FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    existing = None
    if row:
        existing = row[0]

    snapshots = []
    if existing:
        try:
            parsed = json.loads(existing)
            if isinstance(parsed, list):
                snapshots = parsed
            else:
                # if older value was an object, keep it as a single entry
                snapshots = [parsed]
        except Exception:
            snapshots = [{"text": existing}]

    # Normalize data
    if isinstance(data, (dict, list)):
        entry_data = data
    else:
        try:
            entry_data = json.loads(data)
        except Exception:
            entry_data = {"text": str(data)}

    snapshots.append({"date": weather_date, "data": entry_data})
    payload = json.dumps(snapshots)

    cur.execute(
        "UPDATE users SET user_weather = ?, weather_date = ? WHERE username = ?",
        (payload, weather_date, username),
    )
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return bool(changed)


def get_user_weather_date(username: str) -> str | None:
    """Get the date when the user's weather data was last fetched (YYYY-MM-DD format)."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT weather_date FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return row[0]


def get_simulated_temp(user_id: int):
    """Fetches the current simulated temperature for a user."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT sim_inside_temp FROM user_thermostat WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row['sim_inside_temp'] if row else None


def update_simulated_temp(user_id: int, new_temp: float):
    """Updates or inserts the simulated temperature."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_thermostat (user_id, sim_inside_temp, last_updated)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            sim_inside_temp = excluded.sim_inside_temp,
            last_updated = CURRENT_TIMESTAMP
    """, (user_id, new_temp))
    conn.commit()
    conn.close()
