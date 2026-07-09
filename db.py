"""
db.py
Handles all database operations for the Real Estate Follow-Up Bot.
Uses SQLite (a single local file, no server needed).
"""

import os
import sqlite3
from datetime import datetime, date

# On your own PC this defaults to a local file. On Railway, we point this at
# the persistent Volume (via the DB_PATH environment variable) so your client
# data survives redeploys instead of being wiped each time.
DB_PATH = os.environ.get("DB_PATH", "clients.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            interest TEXT,
            stage TEXT DEFAULT 'New',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            note TEXT,
            done INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)

    conn.commit()
    conn.close()


# ---------- CLIENTS ----------

def add_client(name, phone, interest):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO clients (name, phone, interest, created_at) VALUES (?, ?, ?, ?)",
        (name, phone, interest, datetime.now().isoformat()),
    )
    conn.commit()
    client_id = cur.lastrowid
    conn.close()
    return client_id


def get_client(client_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()
    return row


def list_clients():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def search_clients(keyword):
    conn = get_connection()
    cur = conn.cursor()
    like = f"%{keyword}%"
    cur.execute(
        "SELECT * FROM clients WHERE name LIKE ? OR phone LIKE ? ORDER BY created_at DESC",
        (like, like),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def set_stage(client_id, stage):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE clients SET stage = ? WHERE id = ?", (stage, client_id))
    conn.commit()
    conn.close()


def delete_client(client_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM notes WHERE client_id = ?", (client_id,))
    cur.execute("DELETE FROM followups WHERE client_id = ?", (client_id,))
    cur.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    conn.commit()
    conn.close()


# ---------- NOTES ----------

def add_note(client_id, text):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO notes (client_id, text, created_at) VALUES (?, ?, ?)",
        (client_id, text, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_notes(client_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM notes WHERE client_id = ? ORDER BY created_at DESC", (client_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------- FOLLOW-UPS ----------

def add_followup(client_id, due_date, note=""):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO followups (client_id, due_date, note, created_at) VALUES (?, ?, ?, ?)",
        (client_id, due_date, note, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_followups_for_client(client_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM followups WHERE client_id = ? AND done = 0 ORDER BY due_date ASC",
        (client_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_followups_due_on(due_date):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT followups.*, clients.name AS client_name, clients.phone AS client_phone
           FROM followups
           JOIN clients ON clients.id = followups.client_id
           WHERE followups.due_date = ? AND followups.done = 0
           ORDER BY followups.due_date ASC""",
        (due_date,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_followups_between(start_date, end_date):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT followups.*, clients.name AS client_name, clients.phone AS client_phone
           FROM followups
           JOIN clients ON clients.id = followups.client_id
           WHERE followups.due_date BETWEEN ? AND ? AND followups.done = 0
           ORDER BY followups.due_date ASC""",
        (start_date, end_date),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_overdue_followups(today_str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT followups.*, clients.name AS client_name, clients.phone AS client_phone
           FROM followups
           JOIN clients ON clients.id = followups.client_id
           WHERE followups.due_date < ? AND followups.done = 0
           ORDER BY followups.due_date ASC""",
        (today_str,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_followup_done(followup_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE followups SET done = 1 WHERE id = ?", (followup_id,))
    conn.commit()
    conn.close()


def get_followup(followup_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM followups WHERE id = ?", (followup_id,))
    row = cur.fetchone()
    conn.close()
    return row
