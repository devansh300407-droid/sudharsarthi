"""
SUDHARSARTHI - Storage layer (SQLite).

Tables:
- `reports`: Civic issues and road safety hazard reports.
- `users`: Citizen & officer accounts with securely hashed passwords.
- `sessions`: Persistent server-side session tokens with TTL.

Backwards compatible schema upgrades ensure existing reports and databases
are preserved automatically without data loss.
"""
import json
import math
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SUDHARSARTHI_DB", os.path.join(BASE_DIR, "sudharsarthi.db"))
SEED_PATH = os.path.join(BASE_DIR, "seed_reports.json")

_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    title TEXT, category TEXT, location TEXT, landmark TEXT,
    lat REAL, lng REAL,
    priority TEXT, status TEXT, date TEXT,
    reporter TEXT, description TEXT,
    image_url TEXT, after_image_url TEXT,
    estimated_cost INTEGER, dimensions TEXT,
    ai_classification TEXT,
    risk_score INTEGER, original_risk_score INTEGER, score_breakdown TEXT,
    verified_by_inspector INTEGER DEFAULT 0,
    inspector_name TEXT, ward TEXT, contractor TEXT,
    last_updated TEXT,
    duplicate_of TEXT,
    repeat_count INTEGER DEFAULT 0,
    user_id TEXT,
    status_history TEXT,
    repair_notes TEXT,
    citizen_rating INTEGER DEFAULT 0,
    citizen_feedback TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_ward ON reports(ward);
CREATE INDEX IF NOT EXISTS idx_reports_category ON reports(category);
CREATE INDEX IF NOT EXISTS idx_reports_user_id ON reports(user_id);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'citizen',
    created_at TEXT NOT NULL,
    last_login TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""

# camelCase key (API / frontend) -> column name
FIELD_TO_COLUMN = {
    "id": "id",
    "title": "title",
    "category": "category",
    "location": "location",
    "landmark": "landmark",
    "lat": "lat",
    "lng": "lng",
    "priority": "priority",
    "status": "status",
    "date": "date",
    "reporter": "reporter",
    "description": "description",
    "imageUrl": "image_url",
    "afterImageUrl": "after_image_url",
    "estimatedCost": "estimated_cost",
    "dimensions": "dimensions",
    "aiClassification": "ai_classification",
    "riskScore": "risk_score",
    "originalRiskScore": "original_risk_score",
    "scoreBreakdown": "score_breakdown",
    "verifiedByInspector": "verified_by_inspector",
    "inspectorName": "inspector_name",
    "ward": "ward",
    "contractor": "contractor",
    "lastUpdated": "last_updated",
    "duplicateOf": "duplicate_of",
    "repeatCount": "repeat_count",
    "userId": "user_id",
    "statusHistory": "status_history",
    "repairNotes": "repair_notes",
    "citizenRating": "citizen_rating",
    "citizenFeedback": "citizen_feedback",
}
JSON_FIELDS = {"aiClassification", "scoreBreakdown", "statusHistory"}
UPDATABLE = {
    "status",
    "priority",
    "contractor",
    "inspectorName",
    "verifiedByInspector",
    "estimatedCost",
    "afterImageUrl",
    "lastUpdated",
    "ward",
    "riskScore",
    "scoreBreakdown",
    "repeatCount",
    "statusHistory",
    "repairNotes",
    "citizenRating",
    "citizenFeedback",
}


@contextmanager
def connect():
    """Open a SQLite connection, commit on success / roll back on error, and close safely."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initializes tables and applies safe incremental migrations for existing databases."""
    with connect() as conn:
        conn.executescript(SCHEMA)

        # Inspect existing columns in reports table and add any missing ones
        cursor = conn.execute("PRAGMA table_info(reports)")
        existing_cols = {row["name"] for row in cursor.fetchall()}
        new_columns = [
            ("user_id", "TEXT"),
            ("status_history", "TEXT"),
            ("repair_notes", "TEXT"),
            ("citizen_rating", "INTEGER DEFAULT 0"),
            ("citizen_feedback", "TEXT"),
        ]
        for col_name, col_type in new_columns:
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE reports ADD COLUMN {col_name} {col_type}")


def _row_to_report(row):
    out = {}
    row_keys = row.keys()
    for field, col in FIELD_TO_COLUMN.items():
        if col not in row_keys:
            continue
        value = row[col]
        if field in JSON_FIELDS:
            if value:
                try:
                    value = json.loads(value)
                except Exception:
                    value = [] if field == "statusHistory" else {}
            else:
                value = [] if field == "statusHistory" else {}
        elif field == "verifiedByInspector":
            value = bool(value)
        out[field] = value
    return out


def _seq_of(report_id):
    m = re.search(r"(\d+)$", report_id or "")
    return int(m.group(1)) if m else 0


def next_id(year=None):
    """Next sequential tracking ID, e.g. NS-2026-1043."""
    year = year or datetime.now().year
    with connect() as conn:
        row = conn.execute("SELECT MAX(seq) AS m FROM reports").fetchone()
    return f"NS-{year}-{(row['m'] or 1000) + 1}"


def insert_report(report):
    cols, vals = [], []
    for field, col in FIELD_TO_COLUMN.items():
        if field not in report:
            continue
        value = report[field]
        if field in JSON_FIELDS:
            value = json.dumps(value)
        elif field == "verifiedByInspector":
            value = 1 if value else 0
        cols.append(col)
        vals.append(value)
    cols.append("seq")
    vals.append(_seq_of(report["id"]))
    sql = f"INSERT INTO reports ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    with _write_lock, connect() as conn:
        conn.execute(sql, vals)
    return get_report(report["id"])


def get_report(report_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE LOWER(id) = LOWER(?)", (report_id,)
        ).fetchone()
    return _row_to_report(row) if row else None


def list_reports(category=None, priority=None, status=None, ward=None, search=None, user_id=None):
    sql, args = "SELECT * FROM reports WHERE 1=1", []
    for col, val in (
        ("category", category),
        ("priority", priority),
        ("status", status),
        ("ward", ward),
        ("user_id", user_id),
    ):
        if val and val != "All":
            sql += f" AND {col} = ?"
            args.append(val)
    if search:
        like = f"%{search.lower()}%"
        sql += (
            " AND (LOWER(id) LIKE ? OR LOWER(title) LIKE ? OR LOWER(location) LIKE ?"
            " OR LOWER(category) LIKE ? OR LOWER(COALESCE(ward,'')) LIKE ?)"
        )
        args += [like] * 5
    sql += " ORDER BY seq DESC"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_report(r) for r in rows]


def list_reports_by_user(user_id):
    """Retrieve only reports submitted by or associated with a specific user."""
    if not user_id:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reports WHERE user_id = ? ORDER BY seq DESC", (user_id,)
        ).fetchall()
    return [_row_to_report(r) for r in rows]


def update_report(report_id, changes):
    """Apply a whitelisted set of changes. Returns the updated report or None."""
    sets, args = [], []
    for field, value in changes.items():
        if field not in UPDATABLE:
            continue
        if field == "verifiedByInspector":
            value = 1 if value else 0
        elif field in JSON_FIELDS:
            value = json.dumps(value)
        sets.append(f"{FIELD_TO_COLUMN[field]} = ?")
        args.append(value)
    if not sets:
        return get_report(report_id)
    args.append(report_id)
    with _write_lock, connect() as conn:
        cur = conn.execute(f"UPDATE reports SET {', '.join(sets)} WHERE LOWER(id) = LOWER(?)", args)
        if cur.rowcount == 0:
            return None
    return get_report(report_id)


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def find_nearby_open(lat, lng, category, radius_m=25):
    """Unresolved reports of the same category within radius_m of a point (duplicates)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reports WHERE category = ? AND status != 'Resolved'", (category,)
        ).fetchall()
    hits = []
    for row in rows:
        if row["lat"] is None or row["lng"] is None:
            continue
        d = haversine_m(lat, lng, row["lat"], row["lng"])
        if d <= radius_m:
            hits.append((d, _row_to_report(row)))
    hits.sort(key=lambda t: t[0])
    return [r for _, r in hits]


def count():
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM reports").fetchone()["c"]


def seed_if_empty(path=SEED_PATH):
    """Load the demo Ghaziabad reports the first time the database is created."""
    init_db()
    if count() > 0 or not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        reports = json.load(f)
    for r in reports:
        r.setdefault("repeatCount", 0)
        insert_report(r)
    return len(reports)


def reset_demo():
    """Wipe the reports table and reload the demo reports (for presentations)."""
    init_db()
    with _write_lock, connect() as conn:
        conn.execute("DELETE FROM reports")
    return seed_if_empty()


# ------------------------------------------------------------------
# USER AUTHENTICATION & SESSION MANAGEMENT
# ------------------------------------------------------------------

def create_user(name, email, phone, password_hash, role="citizen"):
    """Insert a new user account with normalized email and phone."""
    user_id = f"USR-{uuid.uuid4().hex[:10]}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    email = email.strip().lower()
    phone = re.sub(r"\D", "", phone)
    with _write_lock, connect() as conn:
        conn.execute(
            """INSERT INTO users (id, name, email, phone, password_hash, role, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, name.strip(), email, phone, password_hash, role, now),
        )
    return get_user_by_id(user_id)


def get_user_by_id(user_id):
    """Retrieve safe user profile (without password hash)."""
    if not user_id:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "role": row["role"],
        "createdAt": row["created_at"],
        "lastLogin": row["last_login"],
    }


def get_user_auth(identifier):
    """Retrieve user record including password_hash by normalized email or phone number."""
    if not identifier:
        return None
    norm = identifier.strip().lower()
    phone_digits = re.sub(r"\D", "", identifier)
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM users
               WHERE LOWER(email) = ? OR (phone = ? AND LENGTH(?) >= 10)""",
            (norm, phone_digits, phone_digits),
        ).fetchone()
    return dict(row) if row else None


def update_user_last_login(user_id):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _write_lock, connect() as conn:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))


def create_session(user_id, token, expires_at_iso, ip=None, user_agent=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _write_lock, connect() as conn:
        conn.execute(
            """INSERT INTO sessions (token, user_id, created_at, expires_at, ip, user_agent)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (token, user_id, now, expires_at_iso, (ip or "")[:50], (user_agent or "")[:200]),
        )


def get_session_user(token):
    """Validate token and check expiration; returns safe user dict if valid, else None."""
    if not token:
        return None
    now_iso = datetime.now().isoformat()
    with connect() as conn:
        row = conn.execute(
            """SELECT s.token, s.expires_at, u.id, u.name, u.email, u.phone, u.role, u.created_at, u.last_login
               FROM sessions s
               JOIN users u ON s.user_id = u.id
               WHERE s.token = ?""",
            (token,),
        ).fetchone()
    if not row:
        return None
    if row["expires_at"] < now_iso:
        delete_session(token)
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "role": row["role"],
        "createdAt": row["created_at"],
        "lastLogin": row["last_login"],
    }


def delete_session(token):
    if not token:
        return
    with _write_lock, connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_user_sessions(user_id):
    if not user_id:
        return
    with _write_lock, connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def link_historical_reports(user_id, email, phone):
    """
    Safely link unassigned historical reports to this user if and only if
    ownership can be reliably verified via email or verified phone.
    Per rule: Do not assign historical reports based on a name match alone.
    """
    if not user_id:
        return 0
    linked = 0
    # If any reports have reporter containing the user's verified email or phone
    with _write_lock, connect() as conn:
        if email:
            cur = conn.execute(
                """UPDATE reports SET user_id = ?
                   WHERE user_id IS NULL AND (LOWER(reporter) LIKE ? OR LOWER(description) LIKE ?)""",
                (user_id, f"%{email.lower()}%", f"%{email.lower()}%"),
            )
            linked += cur.rowcount
        if phone and len(phone) >= 10:
            cur = conn.execute(
                """UPDATE reports SET user_id = ?
                   WHERE user_id IS NULL AND (reporter LIKE ? OR description LIKE ?)""",
                (user_id, f"%{phone}%", f"%{phone}%"),
            )
            linked += cur.rowcount
    return linked


if __name__ == "__main__":
    init_db()
    n = seed_if_empty()
    print(f"Database ready at {DB_PATH} - {count()} reports ({n} newly seeded).")
