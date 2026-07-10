"""
db.py
Handles all database operations for the Real Estate Follow-Up Bot.
Uses SQLite (a single local file, no server needed).

Multi-agent support: every client/note/followup belongs to a specific
"agent" (identified by their Telegram user ID). Agents never see each
other's data. Each agent also has a subscription status (trial/active/
revoked) used to control access.
"""

import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.environ.get("DB_PATH", "clients.db")
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT,
            status TEXT DEFAULT 'trial',       -- trial | active | revoked
            trial_end TEXT,
            subscription_end TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
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
            agent_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            agent_id INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            note TEXT,
            done INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)

    conn.commit()
    conn.close()


# ---------- AGENTS (subscribers who use the bot) ----------

def get_agent(telegram_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return row


def register_agent(telegram_id, name):
    """Creates a new agent with a free trial. Returns the agent row."""
    existing = get_agent(telegram_id)
    if existing:
        return existing
    trial_end = (datetime.now() + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO agents (telegram_id, name, status, trial_end, created_at) VALUES (?, ?, 'trial', ?, ?)",
        (telegram_id, name, trial_end, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return get_agent(telegram_id)


def list_agents():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def approve_agent(telegram_id, days):
    """Activates or extends an agent's subscription by `days` from today."""
    sub_end = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agents SET status = 'active', subscription_end = ? WHERE telegram_id = ?",
        (sub_end, telegram_id),
    )
    conn.commit()
    conn.close()


def revoke_agent(telegram_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE agents SET status = 'revoked' WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()


def has_access(agent_row):
    """True if this agent is allowed to use the bot right now."""
    if agent_row is None:
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    if agent_row["status"] == "active":
        return agent_row["subscription_end"] is None or agent_row["subscription_end"] >= today
    if agent_row["status"] == "trial":
        return agent_row["trial_end"] >= today
    return False


# ---------- CLIENTS (scoped to one agent) ----------

def add_client(agent_id, name, phone, interest):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO clients (agent_id, name, phone, interest, created_at) VALUES (?, ?, ?, ?, ?)",
        (agent_id, name, phone, interest, datetime.now().isoformat()),
    )
    conn.commit()
    client_id = cur.lastrowid
    conn.close()
    return client_id


def get_client(client_id, agent_id):
    """Returns the client only if it belongs to this agent (prevents one
    agent from viewing/editing another agent's client by guessing an id)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ? AND agent_id = ?", (client_id, agent_id))
    row = cur.fetchone()
    conn.close()
    return row


def list_clients(agent_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM clients WHERE agent_id = ? ORDER BY created_at DESC", (agent_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def search_clients(agent_id, keyword):
    conn = get_connection()
    cur = conn.cursor()
    like = f"%{keyword}%"
    cur.execute(
        "SELECT * FROM clients WHERE agent_id = ? AND (name LIKE ? OR phone LIKE ?) ORDER BY created_at DESC",
        (agent_id, like, like),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def set_stage(client_id, agent_id, stage):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE clients SET stage = ? WHERE id = ? AND agent_id = ?",
        (stage, client_id, agent_id),
    )
    conn.commit()
    conn.close()


def delete_client(client_id, agent_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM notes WHERE client_id = ? AND agent_id = ?", (client_id, agent_id))
    cur.execute(
        "DELETE FROM followups WHERE client_id = ? AND agent_id = ?", (client_id, agent_id)
    )
    cur.execute("DELETE FROM clients WHERE id = ? AND agent_id = ?", (client_id, agent_id))
    conn.commit()
    conn.close()


# ---------- NOTES ----------

def add_note(client_id, agent_id, text):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO notes (client_id, agent_id, text, created_at) VALUES (?, ?, ?, ?)",
        (client_id, agent_id, text, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_notes(client_id, agent_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM notes WHERE client_id = ? AND agent_id = ? ORDER BY created_at DESC",
        (client_id, agent_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------- FOLLOW-UPS ----------

def add_followup(client_id, agent_id, due_date, note=""):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO followups (client_id, agent_id, due_date, note, created_at) VALUES (?, ?, ?, ?, ?)",
        (client_id, agent_id, due_date, note, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_followups_for_client(client_id, agent_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM followups WHERE client_id = ? AND agent_id = ? AND done = 0 ORDER BY due_date ASC",
        (client_id, agent_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_followups_due_on(agent_id, due_date):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT followups.*, clients.name AS client_name, clients.phone AS client_phone
           FROM followups
           JOIN clients ON clients.id = followups.client_id
           WHERE followups.agent_id = ? AND followups.due_date = ? AND followups.done = 0
           ORDER BY followups.due_date ASC""",
        (agent_id, due_date),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_followups_between(agent_id, start_date, end_date):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT followups.*, clients.name AS client_name, clients.phone AS client_phone
           FROM followups
           JOIN clients ON clients.id = followups.client_id
           WHERE followups.agent_id = ? AND followups.due_date BETWEEN ? AND ? AND followups.done = 0
           ORDER BY followups.due_date ASC""",
        (agent_id, start_date, end_date),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_overdue_followups(agent_id, today_str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT followups.*, clients.name AS client_name, clients.phone AS client_phone
           FROM followups
           JOIN clients ON clients.id = followups.client_id
           WHERE followups.agent_id = ? AND followups.due_date < ? AND followups.done = 0
           ORDER BY followups.due_date ASC""",
        (agent_id, today_str),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_followup_done(followup_id, agent_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE followups SET done = 1 WHERE id = ? AND agent_id = ?", (followup_id, agent_id)
    )
    conn.commit()
    conn.close()


def get_followup(followup_id, agent_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM followups WHERE id = ? AND agent_id = ?", (followup_id, agent_id)
    )
    row = cur.fetchone()
    conn.close()
    return row


def all_active_agent_ids():
    """Used by the daily reminder job to know which agents to message."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM agents")
    rows = cur.fetchall()
    conn.close()
    return [r["telegram_id"] for r in rows]
