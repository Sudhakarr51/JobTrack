"""Database layer: schema, connection handling and every SQL query.

All queries use "?" placeholders, so user input is never pasted into SQL text.
"""
import sqlite3
from pathlib import Path

from flask import current_app, g

STATUSES = ["Wishlist", "Applied", "Assessment", "Interview", "Offer", "Rejected", "Withdrawn"]
JOB_TYPES = ["Full-time", "Internship", "Part-time", "Contract"]
# Statuses where a follow-up still makes sense (not finished yet).
ACTIVE_STATUSES = ["Wishlist", "Applied", "Assessment", "Interview"]

# The ORDER BY text comes from this fixed dictionary, never from the user.
SORT_OPTIONS = {
    "date_desc": ("Applied date (newest first)", "application_date DESC, id DESC"),
    "date_asc": ("Applied date (oldest first)", "application_date ASC, id ASC"),
    "company_asc": ("Company (A-Z)", "company COLLATE NOCASE ASC, id ASC"),
    "company_desc": ("Company (Z-A)", "company COLLATE NOCASE DESC, id DESC"),
}

APPLICATION_FIELDS = [
    "company", "job_title", "job_type", "location", "is_remote", "job_url",
    "application_date", "status", "interview_date", "follow_up_date",
    "salary_range", "notes",
]

# SQLite has no DATE type, so dates are stored as ISO text ("YYYY-MM-DD").
# That format sorts and compares correctly as plain text.
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_applications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company          TEXT NOT NULL,
    job_title        TEXT NOT NULL,
    job_type         TEXT NOT NULL CHECK (job_type IN ({", ".join(repr(t) for t in JOB_TYPES)})),
    location         TEXT NOT NULL DEFAULT '',
    is_remote        INTEGER NOT NULL DEFAULT 0 CHECK (is_remote IN (0, 1)),
    job_url          TEXT NOT NULL DEFAULT '',
    application_date TEXT,
    status           TEXT NOT NULL CHECK (status IN ({", ".join(repr(s) for s in STATUSES)})),
    interview_date   TEXT,
    follow_up_date   TEXT,
    salary_range     TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_apps_user_status  ON job_applications (user_id, status);
CREATE INDEX IF NOT EXISTS idx_apps_user_company ON job_applications (user_id, company);
CREATE INDEX IF NOT EXISTS idx_apps_user_date    ON job_applications (user_id, application_date);
CREATE INDEX IF NOT EXISTS idx_apps_user_follow  ON job_applications (user_id, follow_up_date);
"""


# ---------- connection handling ----------

def get_db():
    """One database connection per request, stored in Flask's `g` object."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row            # lets us use row["column_name"]
        g.db.execute("PRAGMA foreign_keys = ON")  # SQLite ignores foreign keys unless asked
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create the tables if they do not exist yet (safe to run many times)."""
    Path(current_app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    db = get_db()
    db.executescript(SCHEMA)
    db.commit()


# ---------- users ----------

def create_user(username, email, password_hash):
    db = get_db()
    cursor = db.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
        (username, email, password_hash),
    )
    db.commit()
    return cursor.lastrowid


def get_user_by_id(user_id):
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_email(email):
    return get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def username_exists(username):
    row = get_db().execute(
        "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    return row is not None


# ---------- applications (every query is limited to one user_id) ----------

def create_application(user_id, data):
    db = get_db()
    columns = ", ".join(APPLICATION_FIELDS)
    placeholders = ", ".join(":" + f for f in APPLICATION_FIELDS)
    cursor = db.execute(
        f"INSERT INTO job_applications (user_id, {columns}) VALUES (:user_id, {placeholders})",
        {**data, "user_id": user_id},
    )
    db.commit()
    return cursor.lastrowid


def get_application(user_id, app_id):
    """Returns the row only if it belongs to this user - this blocks cross-user access."""
    return get_db().execute(
        "SELECT * FROM job_applications WHERE id = ? AND user_id = ?", (app_id, user_id)
    ).fetchone()


def update_application(user_id, app_id, data):
    db = get_db()
    assignments = ", ".join(f"{f} = :{f}" for f in APPLICATION_FIELDS)
    db.execute(
        f"UPDATE job_applications SET {assignments}, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = :app_id AND user_id = :user_id",
        {**data, "app_id": app_id, "user_id": user_id},
    )
    db.commit()


def delete_application(user_id, app_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM job_applications WHERE id = ? AND user_id = ?", (app_id, user_id)
    )
    db.commit()
    return cursor.rowcount > 0


def _escape_like(text):
    """Make % and _ typed by the user match literally inside LIKE."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_applications(user_id, q="", status="", job_type="", location="",
                        remote_only=False, sort="date_desc", page=1, per_page=10):
    """Search + filter + sort + paginate. Returns (rows, total_count, page)."""
    where = ["user_id = ?"]
    params = [user_id]
    if q:
        like = f"%{_escape_like(q)}%"
        where.append("(company LIKE ? ESCAPE '\\' OR job_title LIKE ? ESCAPE '\\')")
        params += [like, like]
    if status:
        where.append("status = ?")
        params.append(status)
    if job_type:
        where.append("job_type = ?")
        params.append(job_type)
    if location:
        where.append("location LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(location)}%")
    if remote_only:
        where.append("is_remote = 1")

    # where_sql and order_sql only contain fixed text written above;
    # every user-supplied value travels in `params`.
    where_sql = " AND ".join(where)
    order_sql = SORT_OPTIONS.get(sort, SORT_OPTIONS["date_desc"])[1]

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM job_applications WHERE {where_sql}", params).fetchone()[0]
    last_page = max(1, -(-total // per_page))  # ceiling division
    page = min(max(page, 1), last_page)
    rows = db.execute(
        f"SELECT * FROM job_applications WHERE {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return rows, total, page


def all_applications(user_id):
    return get_db().execute(
        "SELECT * FROM job_applications WHERE user_id = ? ORDER BY id", (user_id,)
    ).fetchall()


def dashboard_stats(user_id, today):
    """Everything the dashboard shows, calculated from the database."""
    db = get_db()
    today_text = today.isoformat()

    counts = {status: 0 for status in STATUSES}
    for row in db.execute(
        "SELECT status, COUNT(*) AS n FROM job_applications WHERE user_id = ? GROUP BY status",
        (user_id,),
    ):
        counts[row["status"]] = row["n"]
    total = sum(counts.values())

    # Interview conversion rate = interviews / submitted applications * 100
    #   submitted  = every application except "Wishlist" (not sent yet)
    #   interviews = submitted applications that have an interview date OR are
    #                currently in "Interview" or "Offer" status
    submitted = total - counts["Wishlist"]
    interviews = db.execute(
        "SELECT COUNT(*) FROM job_applications WHERE user_id = ? AND status != 'Wishlist' "
        "AND (interview_date IS NOT NULL OR status IN ('Interview', 'Offer'))",
        (user_id,),
    ).fetchone()[0]
    conversion_rate = round(interviews / submitted * 100, 1) if submitted else 0.0

    active_marks = ", ".join("?" for _ in ACTIVE_STATUSES)  # "?, ?, ?, ?" - fixed text
    upcoming_interviews = db.execute(
        "SELECT * FROM job_applications WHERE user_id = ? AND interview_date >= ? "
        "AND status NOT IN ('Rejected', 'Withdrawn') ORDER BY interview_date LIMIT 5",
        (user_id, today_text),
    ).fetchall()
    upcoming_followups = db.execute(
        f"SELECT * FROM job_applications WHERE user_id = ? AND follow_up_date >= ? "
        f"AND status IN ({active_marks}) ORDER BY follow_up_date LIMIT 5",
        [user_id, today_text, *ACTIVE_STATUSES],
    ).fetchall()
    overdue_followups = db.execute(
        f"SELECT * FROM job_applications WHERE user_id = ? AND follow_up_date < ? "
        f"AND status IN ({active_marks}) ORDER BY follow_up_date",
        [user_id, today_text, *ACTIVE_STATUSES],
    ).fetchall()
    recent = db.execute(
        "SELECT * FROM job_applications WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 5",
        (user_id,),
    ).fetchall()

    return {
        "counts": counts, "total": total, "submitted": submitted,
        "interviews": interviews, "offers": counts["Offer"],
        "conversion_rate": conversion_rate,
        "upcoming_interviews": upcoming_interviews,
        "upcoming_followups": upcoming_followups,
        "overdue_followups": overdue_followups,
        "recent": recent,
    }
