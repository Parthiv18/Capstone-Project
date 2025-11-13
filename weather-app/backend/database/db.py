from pathlib import Path
import sqlite3
import hashlib
import secrets


DB_PATH = Path(__file__).parent / "users.db"


def _ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            postalcode TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _get_conn():
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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


def get_user_postal(username: str) -> str | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT postalcode FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return row[0]
