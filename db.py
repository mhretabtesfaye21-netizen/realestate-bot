"""
db.py — Stage 1: Agencies, Workers, and Clients

Three roles:
  - OWNER: you. Controls everything via .env OWNER_CHAT_ID. Approves/revokes agencies.
  - AGENCY: a real estate company. Must be approved by the owner before anyone
    under it can use the bot. Has a join_code that workers use to join.
  - WORKER: a salesperson who belongs to one agency. Manages their own clients.
    A worker's access depends entirely on their agency being active.

No free trial anymore — access is owner-approved only.
"""

import os
import sqlite3
import random
import string
from datetime import datetime, timedelta

DB_PATH = os.environ.get("DB_PATH", "clients.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS agencies (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            language TEXT DEFAULT 'en',
            status TEXT DEFAULT 'pending',   -- pending | active | revoked
            subscription_end TEXT,
            join_code TEXT UNIQUE,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            telegram_id INTEGER PRIMARY KEY,
            agency_id INTEGER NOT NULL,
            name TEXT,
            language TEXT,   -- NULL = use agency's language
            created_at TEXT,
            FOREIGN KEY (agency_id) REFERENCES agencies (telegram_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER NOT NULL,
            agency_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT,
            interest TEXT,
            status TEXT DEFAULT 'red',   -- red | yellow | green
            client_token TEXT UNIQUE,    -- used in their personal browse link
            client_telegram_id INTEGER,  -- set once the client taps their link
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            worker_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            worker_id INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            note TEXT,
            done INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER NOT NULL,
            photo_file_id TEXT,
            description TEXT NOT NULL,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS interests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            property_id INTEGER NOT NULL,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# ---------- AGENCIES ----------

def _generate_join_code(cur):
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        cur.execute("SELECT 1 FROM agencies WHERE join_code = ?", (code,))
        if not cur.fetchone():
            return code


def register_agency(telegram_id, name):
    existing = get_agency(telegram_id)
    if existing:
        return existing
    conn = get_connection()
    cur = conn.cursor()
    code = _generate_join_code(cur)
    cur.execute(
        "INSERT INTO agencies (telegram_id, name, status, join_code, created_at) "
        "VALUES (?, ?, 'pending', ?, ?)",
        (telegram_id, name, code, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return get_agency(telegram_id)


def get_agency(telegram_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agencies WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_agency_by_join_code(code):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agencies WHERE join_code = ?", (code.upper(),))
    row = cur.fetchone()
    conn.close()
    return row


def list_agencies():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agencies ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def list_pending_agencies():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agencies WHERE status = 'pending' ORDER BY created_at ASC")
    rows = cur.fetchall()
    conn.close()
    return rows


def approve_agency(telegram_id, days):
    sub_end = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agencies SET status = 'active', subscription_end = ? WHERE telegram_id = ?",
        (sub_end, telegram_id),
    )
    conn.commit()
    conn.close()


def revoke_agency(telegram_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE agencies SET status = 'revoked' WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()


def set_agency_language(telegram_id, language):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agencies SET language = ? WHERE telegram_id = ?", (language, telegram_id)
    )
    conn.commit()
    conn.close()


def agency_has_access(agency_row):
    if agency_row is None:
        return False
    if agency_row["status"] != "active":
        return False
    if agency_row["subscription_end"] is None:
        return True
    today = datetime.now().strftime("%Y-%m-%d")
    return agency_row["subscription_end"] >= today


# ---------- WORKERS ----------

def register_worker(telegram_id, agency_id, name):
    existing = get_worker(telegram_id)
    if existing:
        return existing
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO workers (telegram_id, agency_id, name, created_at) VALUES (?, ?, ?, ?)",
        (telegram_id, agency_id, name, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return get_worker(telegram_id)


def get_worker(telegram_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM workers WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return row


def list_workers_for_agency(agency_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM workers WHERE agency_id = ? ORDER BY created_at DESC", (agency_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def worker_has_access(worker_row):
    """A worker's access depends entirely on their agency being active."""
    if worker_row is None:
        return False
    agency = get_agency(worker_row["agency_id"])
    return agency_has_access(agency)


def set_worker_language(telegram_id, language):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE workers SET language = ? WHERE telegram_id = ?", (language, telegram_id)
    )
    conn.commit()
    conn.close()


# ---------- CLIENTS (scoped to one worker) ----------

def _generate_client_token(cur):
    while True:
        token = "".join(random.choices(string.ascii_letters + string.digits, k=12))
        cur.execute("SELECT 1 FROM clients WHERE client_token = ?", (token,))
        if not cur.fetchone():
            return token


def add_client(worker_id, agency_id, name, phone, interest):
    conn = get_connection()
    cur = conn.cursor()
    token = _generate_client_token(cur)
    cur.execute(
        "INSERT INTO clients (worker_id, agency_id, name, phone, interest, client_token, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (worker_id, agency_id, name, phone, interest, token, datetime.now().isoformat()),
    )
    conn.commit()
    client_id = cur.lastrowid
    conn.close()
    return client_id


def get_client_by_token(token):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE client_token = ?", (token,))
    row = cur.fetchone()
    conn.close()
    return row


def get_client_by_id(client_id):
    """Unscoped lookup (no worker_id filter) — used for the client-facing
    browse flow, where the visitor isn't a registered worker."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()
    return row


def set_client_telegram_id(client_id, telegram_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE clients SET client_telegram_id = ? WHERE id = ?", (telegram_id, client_id)
    )
    conn.commit()
    conn.close()


def get_client(client_id, worker_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM clients WHERE id = ? AND worker_id = ?", (client_id, worker_id)
    )
    row = cur.fetchone()
    conn.close()
    return row


def list_clients(worker_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM clients WHERE worker_id = ? ORDER BY created_at DESC", (worker_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def search_clients(worker_id, keyword):
    conn = get_connection()
    cur = conn.cursor()
    like = f"%{keyword}%"
    cur.execute(
        "SELECT * FROM clients WHERE worker_id = ? AND (name LIKE ? OR phone LIKE ?) "
        "ORDER BY created_at DESC",
        (worker_id, like, like),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def set_client_status(client_id, worker_id, status):
    """status must be 'red', 'yellow', or 'green'."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE clients SET status = ? WHERE id = ? AND worker_id = ?",
        (status, client_id, worker_id),
    )
    conn.commit()
    conn.close()


def delete_client(client_id, worker_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM notes WHERE client_id = ? AND worker_id = ?", (client_id, worker_id))
    cur.execute(
        "DELETE FROM followups WHERE client_id = ? AND worker_id = ?", (client_id, worker_id)
    )
    cur.execute("DELETE FROM clients WHERE id = ? AND worker_id = ?", (client_id, worker_id))
    conn.commit()
    conn.close()


# ---------- NOTES ----------

def add_note(client_id, worker_id, text):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO notes (client_id, worker_id, text, created_at) VALUES (?, ?, ?, ?)",
        (client_id, worker_id, text, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_notes(client_id, worker_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM notes WHERE client_id = ? AND worker_id = ? ORDER BY created_at DESC",
        (client_id, worker_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------- FOLLOW-UPS ----------

def add_followup(client_id, worker_id, due_date, note=""):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO followups (client_id, worker_id, due_date, note, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (client_id, worker_id, due_date, note, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_followups_for_client(client_id, worker_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM followups WHERE client_id = ? AND worker_id = ? AND done = 0 "
        "ORDER BY due_date ASC",
        (client_id, worker_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_followups_due_on(worker_id, due_date):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT followups.*, clients.name AS client_name, clients.phone AS client_phone,
                  clients.status AS client_status
           FROM followups
           JOIN clients ON clients.id = followups.client_id
           WHERE followups.worker_id = ? AND followups.due_date = ? AND followups.done = 0
           ORDER BY followups.due_date ASC""",
        (worker_id, due_date),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_followups_between(worker_id, start_date, end_date):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT followups.*, clients.name AS client_name, clients.phone AS client_phone,
                  clients.status AS client_status
           FROM followups
           JOIN clients ON clients.id = followups.client_id
           WHERE followups.worker_id = ? AND followups.due_date BETWEEN ? AND ?
                 AND followups.done = 0
           ORDER BY followups.due_date ASC""",
        (worker_id, start_date, end_date),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_overdue_followups(worker_id, today_str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT followups.*, clients.name AS client_name, clients.phone AS client_phone,
                  clients.status AS client_status
           FROM followups
           JOIN clients ON clients.id = followups.client_id
           WHERE followups.worker_id = ? AND followups.due_date < ? AND followups.done = 0
           ORDER BY followups.due_date ASC""",
        (worker_id, today_str),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_followup_done(followup_id, worker_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE followups SET done = 1 WHERE id = ? AND worker_id = ?",
        (followup_id, worker_id),
    )
    conn.commit()
    conn.close()


def get_followup(followup_id, worker_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM followups WHERE id = ? AND worker_id = ?", (followup_id, worker_id)
    )
    row = cur.fetchone()
    conn.close()
    return row


def all_worker_ids():
    """Used by the daily reminder job."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM workers")
    rows = cur.fetchall()
    conn.close()
    return [r["telegram_id"] for r in rows]


# ---------- PROPERTIES ----------

def add_property(agency_id, photo_file_id, description):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO properties (agency_id, photo_file_id, description, created_at) "
        "VALUES (?, ?, ?, ?)",
        (agency_id, photo_file_id, description, datetime.now().isoformat()),
    )
    conn.commit()
    property_id = cur.lastrowid
    conn.close()
    return property_id


def list_properties(agency_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM properties WHERE agency_id = ? ORDER BY created_at DESC", (agency_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_property(property_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM properties WHERE id = ?", (property_id,))
    row = cur.fetchone()
    conn.close()
    return row


def delete_property(property_id, agency_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM properties WHERE id = ? AND agency_id = ?", (property_id, agency_id)
    )
    conn.commit()
    conn.close()


# ---------- INTERESTS (a client tapping "interested" on a property) ----------

def add_interest(client_id, property_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO interests (client_id, property_id, created_at) VALUES (?, ?, ?)",
        (client_id, property_id, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def list_interests_for_agency(agency_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT interests.*, clients.name AS client_name, clients.worker_id AS worker_id,
                  properties.description AS property_description
           FROM interests
           JOIN clients ON clients.id = interests.client_id
           JOIN properties ON properties.id = interests.property_id
           WHERE clients.agency_id = ?
           ORDER BY interests.created_at DESC""",
        (agency_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows
