"""
bot.py
A Telegram bot for real estate agents to manage clients and follow-ups.
Multi-agent (multi-tenant): every agent who messages this bot gets their
own private, separate client list. New agents get a free trial; you (the
owner) approve payment manually and extend their access with /approve.

Agent commands:
  /start              - register / welcome
  /addclient          - add a new client (guided, step by step)
  /clients            - list all clients
  /find <keyword>     - search clients by name or phone
  /view <id>          - view full client details, notes, and follow-ups
  /note <id> <text>   - add a note to a client
  /followup <id> <days> <text>  - schedule a follow-up N days from now
  /today              - show today's follow-ups
  /week               - show follow-ups in the next 7 days
  /overdue            - show follow-ups that were missed
  /done <followup_id> - mark a follow-up as completed
  /delete <id>        - delete a client permanently
  /status             - check your own trial/subscription status
  /cancel             - cancel the current guided step (e.g. /addclient)

Owner-only commands (only work for OWNER_CHAT_ID):
  /agents                    - list every agent and their subscription status
  /approve <telegram_id> <days> - activate/extend an agent's subscription
  /revoke <telegram_id>      - cut off an agent's access
  /backup                    - download the current database file

Setup:
  1. pip install -r requirements.txt
  2. Copy .env.example to .env and fill in BOT_TOKEN and OWNER_CHAT_ID
  3. python bot.py
"""

import os
import logging
from datetime import datetime, timedelta, time as dtime

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID")  # you - gets daily reminders + admin powers
REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR", "9"))  # 24h format, server time
BANK_INFO = os.environ.get("BANK_INFO", "")        # e.g. "CBE 1000123456789, Tizita Dachew"
TELEBIRR_NUMBER = os.environ.get("TELEBIRR_NUMBER", "")  # e.g. "0911223344"
MONTHLY_PRICE = os.environ.get("MONTHLY_PRICE", "")       # e.g. "300 birr"
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))

# Conversation states for /addclient
NAME, PHONE, INTEREST = range(3)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_client(row):
    return (
        f"#{row['id']} — {row['name']}\n"
        f"Phone: {row['phone'] or '-'}\n"
        f"Interested in: {row['interest'] or '-'}\n"
        f"Stage: {row['stage']}"
    )


def fmt_followup_line(row):
    return f"• #{row['id']} {row['due_date']} — {row['client_name']} ({row['client_phone'] or 'no phone'}): {row['note'] or ''}"


def is_owner(update: Update) -> bool:
    return OWNER_CHAT_ID is not None and str(update.effective_user.id) == str(OWNER_CHAT_ID)


def payment_instructions() -> str:
    lines = []
    if MONTHLY_PRICE:
        lines.append(f"💳 Price: {MONTHLY_PRICE}/month")
    if BANK_INFO:
        lines.append(f"🏦 Bank: {BANK_INFO}")
    if TELEBIRR_NUMBER:
        lines.append(f"📱 Telebirr: {TELEBIRR_NUMBER}")
    if not lines:
        return "\n\nPlease contact the bot owner for payment details."
    return (
        "\n\n"
        + "\n".join(lines)
        + "\n\nAfter paying, send your Telegram ID (shown in /status) to the owner so they can activate your account."
    )


async def require_access(update: Update) -> bool:
    """Registers the agent if new, and checks they're allowed to use the bot.
    Sends a message and returns False if access is denied."""
    user = update.effective_user
    agent = db.get_agent(user.id)
    if agent is None:
        agent = db.register_agent(user.id, user.full_name)
        await update.message.reply_text(
            f"👋 Welcome, {user.first_name}! You have a free {TRIAL_DAYS}-day trial, "
            f"until {agent['trial_end']}.\n\nSend /addclient to add your first client."
        )
        return True

    if not db.has_access(agent):
        await update.message.reply_text(
            "⏰ Your trial or subscription has ended." + payment_instructions()
        )
        return False

    return True


# ---------------------------------------------------------------------------
# Basic commands
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "Here's what I can do:\n"
    "/addclient - add a new client\n"
    "/clients - list all clients\n"
    "/find <keyword> - search by name or phone\n"
    "/view <id> - see full client details\n"
    "/note <id> <text> - add a note\n"
    "/stage <id> <stage> - update pipeline stage (e.g. New, Contacted, Negotiating, Closed)\n"
    "/followup <id> <days> <text> - schedule a follow-up\n"
    "/today - today's follow-ups\n"
    "/week - follow-ups in next 7 days\n"
    "/overdue - missed follow-ups\n"
    "/done <followup_id> - mark follow-up complete\n"
    "/delete <id> - remove a client\n"
    "/status - check your trial/subscription status\n"
    "/help - show this list again anytime\n"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    await update.message.reply_text(HELP_TEXT)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    agent = db.get_agent(user.id)
    owner_note = "\n👑 You are the Owner — you also have /agents, /approve, /revoke\n" if is_owner(update) else ""

    if agent is None:
        agent = db.register_agent(user.id, user.full_name)
        await update.message.reply_text(
            f"👋 Welcome to your Real Estate Client Assistant, {user.first_name}!\n"
            f"{owner_note}\n"
            f"Your Telegram ID is: {user.id}\n"
            f"You're on a free trial until {agent['trial_end']}.\n\n"
            f"{HELP_TEXT}"
        )
        return

    status_line = (
        f"Active until {agent['subscription_end']}"
        if agent["status"] == "active"
        else f"Trial until {agent['trial_end']}"
        if agent["status"] == "trial"
        else "Access revoked"
    )
    await update.message.reply_text(
        f"👋 Welcome back, {user.first_name}!\n"
        f"{owner_note}"
        f"Your Telegram ID is: {user.id}\n"
        f"Status: {status_line}\n\n"
        "/clients - list all clients\n"
        "/addclient - add a new client\n"
        "/today - today's follow-ups\n"
        "/help - see the full command list\n"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def my_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    agent = db.get_agent(user.id)
    if agent is None:
        await update.message.reply_text("You haven't registered yet — send /start first.")
        return

    if agent["status"] == "active":
        msg = f"✅ Your subscription is active until {agent['subscription_end']}."
    elif agent["status"] == "trial":
        days_left = (
            datetime.strptime(agent["trial_end"], "%Y-%m-%d") - datetime.now()
        ).days
        if db.has_access(agent):
            msg = f"🕐 You're on a free trial until {agent['trial_end']} ({max(days_left, 0)} day(s) left)."
        else:
            msg = f"⏰ Your free trial ended on {agent['trial_end']}.\n\n{PAYMENT_INFO}"
    else:
        msg = f"🚫 Your access has been revoked.\n\n{PAYMENT_INFO}"

    await update.message.reply_text(msg)


# ---------------------------------------------------------------------------
# /addclient conversation
# ---------------------------------------------------------------------------

async def addclient_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return ConversationHandler.END
    await update.message.reply_text("Let's add a new client. What's their name?")
    return NAME


async def addclient_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Phone number? (or send - to skip)")
    return PHONE


async def addclient_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["phone"] = "" if text == "-" else text
    await update.message.reply_text(
        "What are they interested in? (e.g. '2BR apartment downtown', or - to skip)"
    )
    return INTEREST


async def addclient_interest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    interest = "" if text == "-" else text
    agent_id = update.effective_user.id
    client_id = db.add_client(
        agent_id, context.user_data["name"], context.user_data["phone"], interest
    )
    await update.message.reply_text(
        f"✅ Client added as #{client_id}: {context.user_data['name']}\n\n"
        f"Tip: schedule a first follow-up with:\n/followup {client_id} 3 First call"
    )
    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Clients: list / search / view / delete
# ---------------------------------------------------------------------------

async def clients_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    agent_id = update.effective_user.id
    rows = db.list_clients(agent_id)
    if not rows:
        await update.message.reply_text("No clients yet. Add one with /addclient")
        return
    lines = [f"#{r['id']} {r['name']} — {r['phone'] or 'no phone'} ({r['stage']})" for r in rows]
    await update.message.reply_text("👥 Clients:\n" + "\n".join(lines))


async def clients_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /find <name or phone>")
        return
    agent_id = update.effective_user.id
    keyword = " ".join(context.args)
    rows = db.search_clients(agent_id, keyword)
    if not rows:
        await update.message.reply_text("No matches found.")
        return
    lines = [f"#{r['id']} {r['name']} — {r['phone'] or 'no phone'} ({r['stage']})" for r in rows]
    await update.message.reply_text("🔎 Matches:\n" + "\n".join(lines))


async def client_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /view <client_id>")
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return

    agent_id = update.effective_user.id
    client = db.get_client(client_id, agent_id)
    if not client:
        await update.message.reply_text("Client not found.")
        return

    msg = fmt_client(client) + "\n\n"

    followups = db.get_followups_for_client(client_id, agent_id)
    if followups:
        msg += "📅 Upcoming follow-ups:\n"
        msg += "\n".join(f"  #{f['id']} {f['due_date']} — {f['note'] or ''}" for f in followups)
        msg += "\n\n"
    else:
        msg += "📅 No upcoming follow-ups.\n\n"

    notes = db.get_notes(client_id, agent_id)
    if notes:
        msg += "📝 Notes (latest first):\n"
        msg += "\n".join(f"  [{n['created_at'][:16]}] {n['text']}" for n in notes[:10])
    else:
        msg += "📝 No notes yet."

    await update.message.reply_text(msg)


async def client_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /delete <client_id>")
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return
    agent_id = update.effective_user.id
    client = db.get_client(client_id, agent_id)
    if not client:
        await update.message.reply_text("Client not found.")
        return
    db.delete_client(client_id, agent_id)
    await update.message.reply_text(f"🗑️ Deleted client #{client_id} ({client['name']}).")


async def client_stage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /stage <client_id> <stage>\n"
            "Example: /stage 3 Negotiating\n"
            "Common stages: New, Contacted, Negotiating, Closed, Lost — "
            "but you can use any word you like."
        )
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return
    agent_id = update.effective_user.id
    client = db.get_client(client_id, agent_id)
    if not client:
        await update.message.reply_text("Client not found.")
        return
    stage = " ".join(context.args[1:])
    db.set_stage(client_id, agent_id, stage)
    await update.message.reply_text(f"📊 {client['name']} is now marked as: {stage}")


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

async def note_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /note <client_id> <text>")
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return
    agent_id = update.effective_user.id
    client = db.get_client(client_id, agent_id)
    if not client:
        await update.message.reply_text("Client not found.")
        return
    text = " ".join(context.args[1:])
    db.add_note(client_id, agent_id, text)
    await update.message.reply_text(f"📝 Note added to {client['name']}.")


# ---------------------------------------------------------------------------
# Follow-ups
# ---------------------------------------------------------------------------

async def followup_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /followup <client_id> <days_from_now> <note>\n"
            "Example: /followup 3 7 Call about the villa listing"
        )
        return
    try:
        client_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Client id and days must be numbers.")
        return

    agent_id = update.effective_user.id
    client = db.get_client(client_id, agent_id)
    if not client:
        await update.message.reply_text("Client not found.")
        return

    note = " ".join(context.args[2:]) if len(context.args) > 2 else "Follow-up call"
    due_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    db.add_followup(client_id, agent_id, due_date, note)
    await update.message.reply_text(
        f"📅 Follow-up scheduled for {client['name']} on {due_date}."
    )


async def followups_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    agent_id = update.effective_user.id
    today_str = datetime.now().strftime("%Y-%m-%d")
    rows = db.get_followups_due_on(agent_id, today_str)
    if not rows:
        await update.message.reply_text("✅ No follow-ups due today.")
        return
    lines = [fmt_followup_line(r) for r in rows]
    await update.message.reply_text("📅 Today's follow-ups:\n" + "\n".join(lines))


async def followups_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    agent_id = update.effective_user.id
    today = datetime.now()
    end = today + timedelta(days=7)
    rows = db.get_followups_between(
        agent_id, today.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    if not rows:
        await update.message.reply_text("No follow-ups in the next 7 days.")
        return
    lines = [fmt_followup_line(r) for r in rows]
    await update.message.reply_text("📅 Next 7 days:\n" + "\n".join(lines))


async def followups_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    agent_id = update.effective_user.id
    today_str = datetime.now().strftime("%Y-%m-%d")
    rows = db.get_overdue_followups(agent_id, today_str)
    if not rows:
        await update.message.reply_text("🎉 Nothing overdue.")
        return
    lines = [fmt_followup_line(r) for r in rows]
    await update.message.reply_text("⚠️ Overdue follow-ups:\n" + "\n".join(lines))


async def followup_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_access(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /done <followup_id>")
        return
    try:
        followup_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Follow-up id must be a number.")
        return
    agent_id = update.effective_user.id
    followup = db.get_followup(followup_id, agent_id)
    if not followup:
        await update.message.reply_text("Follow-up not found.")
        return
    db.mark_followup_done(followup_id, agent_id)
    await update.message.reply_text(
        f"✅ Marked follow-up #{followup_id} as done.\n"
        f"Tip: schedule the next one with /followup {followup['client_id']} <days> <note>"
    )


# ---------------------------------------------------------------------------
# Owner-only: manage agents / subscriptions
# ---------------------------------------------------------------------------

async def agents_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("This command is owner-only.")
        return
    rows = db.list_agents()
    if not rows:
        await update.message.reply_text("No agents registered yet.")
        return
    lines = []
    for r in rows:
        status = r["status"]
        detail = r["subscription_end"] if status == "active" else r["trial_end"]
        owner_tag = " 👑 (you, the owner)" if str(r["telegram_id"]) == str(OWNER_CHAT_ID) else ""
        lines.append(f"• {r['telegram_id']} — {r['name']} — {status} (until {detail}){owner_tag}")
    await update.message.reply_text("👥 Agents:\n" + "\n".join(lines))


async def agent_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("This command is owner-only.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /approve <telegram_id> <days>")
        return
    try:
        telegram_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("telegram_id and days must be numbers.")
        return
    agent = db.get_agent(telegram_id)
    if not agent:
        await update.message.reply_text("No agent with that Telegram ID has messaged the bot yet.")
        return
    db.approve_agent(telegram_id, days)
    await update.message.reply_text(f"✅ Approved agent {telegram_id} for {days} days.")
    try:
        await context.bot.send_message(
            chat_id=telegram_id,
            text=f"🎉 Your subscription is now active for {days} days. Thanks for subscribing!",
        )
    except Exception:
        logger.warning("Could not notify agent %s of approval", telegram_id)


async def agent_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("This command is owner-only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /revoke <telegram_id>")
        return
    try:
        telegram_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("telegram_id must be a number.")
        return
    db.revoke_agent(telegram_id)
    await update.message.reply_text(f"🚫 Revoked access for agent {telegram_id}.")


async def my_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lets any agent check their own trial/subscription status."""
    user = update.effective_user
    agent = db.get_agent(user.id)
    if agent is None:
        agent = db.register_agent(user.id, user.full_name)
        await update.message.reply_text(
            f"You're brand new here! Free trial until {agent['trial_end']}.\n"
            f"Your Telegram ID: {user.id}"
        )
        return

    if agent["status"] == "active":
        msg = f"✅ Active subscription until {agent['subscription_end']}."
    elif agent["status"] == "trial" and db.has_access(agent):
        msg = f"🕐 Free trial — {agent['trial_end']} is your last day."
    elif agent["status"] == "trial":
        msg = f"⏰ Your trial ended on {agent['trial_end']}." + payment_instructions()
    else:
        msg = "🚫 Your access has been revoked." + payment_instructions()

    await update.message.reply_text(f"Your Telegram ID: {user.id}\n\n{msg}")


async def send_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: sends the current database file as a downloadable backup."""
    if not is_owner(update):
        await update.message.reply_text("This command is owner-only.")
        return
    if not os.path.exists(db.DB_PATH):
        await update.message.reply_text("No database file found yet.")
        return
    try:
        with open(db.DB_PATH, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"clients_backup_{datetime.now().strftime('%Y-%m-%d')}.db",
                caption="📦 Here's your current backup. Keep it somewhere safe.",
            )
    except Exception as e:
        logger.warning("Backup failed: %s", e)
        await update.message.reply_text("Something went wrong sending the backup.")


async def backup_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("This command is owner-only.")
        return
    if not os.path.exists(db.DB_PATH):
        await update.message.reply_text("No database file found yet.")
        return
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    await update.message.reply_document(
        document=open(db.DB_PATH, "rb"),
        filename=f"clients_backup_{stamp}.db",
        caption="📦 Here's your current backup. Save it somewhere safe.",
    )


# ---------------------------------------------------------------------------
# Daily reminder job (runs once per day, messages every agent with due items)
# ---------------------------------------------------------------------------

async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE):
    today_str = datetime.now().strftime("%Y-%m-%d")

    for agent_id in db.all_active_agent_ids():
        agent = db.get_agent(agent_id)
        if not db.has_access(agent):
            continue

        due_today = db.get_followups_due_on(agent_id, today_str)
        overdue = db.get_overdue_followups(agent_id, today_str)

        if not due_today and not overdue:
            continue

        msg = "☀️ Good morning! Here's your follow-up list:\n\n"
        if due_today:
            msg += "📅 Due today:\n" + "\n".join(fmt_followup_line(r) for r in due_today) + "\n\n"
        if overdue:
            msg += "⚠️ Overdue:\n" + "\n".join(fmt_followup_line(r) for r in overdue)

        try:
            await context.bot.send_message(chat_id=agent_id, text=msg)
        except Exception:
            logger.warning("Could not send daily reminder to agent %s", agent_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN is not set. Copy .env.example to .env and fill it in, "
            "or export BOT_TOKEN before running."
        )

    db.init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    addclient_conv = ConversationHandler(
        entry_points=[CommandHandler("addclient", addclient_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addclient_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addclient_phone)],
            INTEREST: [MessageHandler(filters.TEXT & ~filters.COMMAND, addclient_interest)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", my_status))
    application.add_handler(addclient_conv)
    application.add_handler(CommandHandler("clients", clients_list))
    application.add_handler(CommandHandler("find", clients_find))
    application.add_handler(CommandHandler("view", client_view))
    application.add_handler(CommandHandler("delete", client_delete))
    application.add_handler(CommandHandler("stage", client_stage))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("note", note_add))
    application.add_handler(CommandHandler("followup", followup_add))
    application.add_handler(CommandHandler("today", followups_today))
    application.add_handler(CommandHandler("week", followups_week))
    application.add_handler(CommandHandler("overdue", followups_overdue))
    application.add_handler(CommandHandler("done", followup_done))

    # Owner-only
    application.add_handler(CommandHandler("agents", agents_list))
    application.add_handler(CommandHandler("approve", agent_approve))
    application.add_handler(CommandHandler("revoke", agent_revoke))
    application.add_handler(CommandHandler("backup", send_backup))

    # Any agent can check their own status
    application.add_handler(CommandHandler("status", my_status))
    application.add_handler(CommandHandler("backup", backup_now))

    # Daily reminder job (requires: pip install "python-telegram-bot[job-queue]")
    if application.job_queue:
        application.job_queue.run_daily(
            send_daily_reminders, time=dtime(hour=REMINDER_HOUR, minute=0)
        )
    else:
        logger.warning(
            "JobQueue not available - daily reminders disabled. "
            'Install with: pip install "python-telegram-bot[job-queue]"'
        )

    logger.info("Bot starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
