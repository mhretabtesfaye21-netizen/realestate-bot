"""
bot.py
A Telegram bot for real estate salespeople to manage clients and follow-ups.

Commands:
  /start              - welcome message and help
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
  /cancel             - cancel the current guided step (e.g. /addclient)

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
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID")  # where daily reminders are sent
REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR", "9"))  # 24h format, server time

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


# ---------------------------------------------------------------------------
# Basic commands
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "👋 Welcome to your Real Estate Client Assistant!\n\n"
        f"Your chat ID is: {chat_id}\n"
        "(If you haven't already, put this in your .env as OWNER_CHAT_ID so daily reminders reach you.)\n\n"
        "Here's what I can do:\n"
        "/addclient - add a new client\n"
        "/clients - list all clients\n"
        "/find <keyword> - search by name or phone\n"
        "/view <id> - see full client details\n"
        "/note <id> <text> - add a note\n"
        "/followup <id> <days> <text> - schedule a follow-up\n"
        "/today - today's follow-ups\n"
        "/week - follow-ups in next 7 days\n"
        "/overdue - missed follow-ups\n"
        "/done <followup_id> - mark follow-up complete\n"
        "/delete <id> - remove a client\n"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /addclient conversation
# ---------------------------------------------------------------------------

async def addclient_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    client_id = db.add_client(
        context.user_data["name"], context.user_data["phone"], interest
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
    rows = db.list_clients()
    if not rows:
        await update.message.reply_text("No clients yet. Add one with /addclient")
        return
    lines = [f"#{r['id']} {r['name']} — {r['phone'] or 'no phone'} ({r['stage']})" for r in rows]
    await update.message.reply_text("👥 Clients:\n" + "\n".join(lines))


async def clients_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /find <name or phone>")
        return
    keyword = " ".join(context.args)
    rows = db.search_clients(keyword)
    if not rows:
        await update.message.reply_text("No matches found.")
        return
    lines = [f"#{r['id']} {r['name']} — {r['phone'] or 'no phone'} ({r['stage']})" for r in rows]
    await update.message.reply_text("🔎 Matches:\n" + "\n".join(lines))


async def client_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /view <client_id>")
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return

    client = db.get_client(client_id)
    if not client:
        await update.message.reply_text("Client not found.")
        return

    msg = fmt_client(client) + "\n\n"

    followups = db.get_followups_for_client(client_id)
    if followups:
        msg += "📅 Upcoming follow-ups:\n"
        msg += "\n".join(f"  #{f['id']} {f['due_date']} — {f['note'] or ''}" for f in followups)
        msg += "\n\n"
    else:
        msg += "📅 No upcoming follow-ups.\n\n"

    notes = db.get_notes(client_id)
    if notes:
        msg += "📝 Notes (latest first):\n"
        msg += "\n".join(f"  [{n['created_at'][:16]}] {n['text']}" for n in notes[:10])
    else:
        msg += "📝 No notes yet."

    await update.message.reply_text(msg)


async def client_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /delete <client_id>")
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return
    client = db.get_client(client_id)
    if not client:
        await update.message.reply_text("Client not found.")
        return
    db.delete_client(client_id)
    await update.message.reply_text(f"🗑️ Deleted client #{client_id} ({client['name']}).")


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

async def note_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /note <client_id> <text>")
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return
    client = db.get_client(client_id)
    if not client:
        await update.message.reply_text("Client not found.")
        return
    text = " ".join(context.args[1:])
    db.add_note(client_id, text)
    await update.message.reply_text(f"📝 Note added to {client['name']}.")


# ---------------------------------------------------------------------------
# Follow-ups
# ---------------------------------------------------------------------------

async def followup_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    client = db.get_client(client_id)
    if not client:
        await update.message.reply_text("Client not found.")
        return

    note = " ".join(context.args[2:]) if len(context.args) > 2 else ""
    due_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    db.add_followup(client_id, due_date, note)
    await update.message.reply_text(
        f"📅 Follow-up scheduled for {client['name']} on {due_date}."
    )


async def followups_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today_str = datetime.now().strftime("%Y-%m-%d")
    rows = db.get_followups_due_on(today_str)
    if not rows:
        await update.message.reply_text("✅ No follow-ups due today.")
        return
    lines = [fmt_followup_line(r) for r in rows]
    await update.message.reply_text("📅 Today's follow-ups:\n" + "\n".join(lines))


async def followups_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now()
    end = today + timedelta(days=7)
    rows = db.get_followups_between(today.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if not rows:
        await update.message.reply_text("No follow-ups in the next 7 days.")
        return
    lines = [fmt_followup_line(r) for r in rows]
    await update.message.reply_text("📅 Next 7 days:\n" + "\n".join(lines))


async def followups_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today_str = datetime.now().strftime("%Y-%m-%d")
    rows = db.get_overdue_followups(today_str)
    if not rows:
        await update.message.reply_text("🎉 Nothing overdue.")
        return
    lines = [fmt_followup_line(r) for r in rows]
    await update.message.reply_text("⚠️ Overdue follow-ups:\n" + "\n".join(lines))


async def followup_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /done <followup_id>")
        return
    try:
        followup_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Follow-up id must be a number.")
        return
    followup = db.get_followup(followup_id)
    if not followup:
        await update.message.reply_text("Follow-up not found.")
        return
    db.mark_followup_done(followup_id)
    await update.message.reply_text(
        f"✅ Marked follow-up #{followup_id} as done.\n"
        f"Tip: schedule the next one with /followup {followup['client_id']} <days> <note>"
    )


# ---------------------------------------------------------------------------
# Daily reminder job
# ---------------------------------------------------------------------------

async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE):
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID not set - skipping daily reminder.")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    due_today = db.get_followups_due_on(today_str)
    overdue = db.get_overdue_followups(today_str)

    if not due_today and not overdue:
        return  # nothing to say, don't spam

    msg = "☀️ Good morning! Here's your follow-up list:\n\n"
    if due_today:
        msg += "📅 Due today:\n" + "\n".join(fmt_followup_line(r) for r in due_today) + "\n\n"
    if overdue:
        msg += "⚠️ Overdue:\n" + "\n".join(fmt_followup_line(r) for r in overdue)

    await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=msg)


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
    application.add_handler(addclient_conv)
    application.add_handler(CommandHandler("clients", clients_list))
    application.add_handler(CommandHandler("find", clients_find))
    application.add_handler(CommandHandler("view", client_view))
    application.add_handler(CommandHandler("delete", client_delete))
    application.add_handler(CommandHandler("note", note_add))
    application.add_handler(CommandHandler("followup", followup_add))
    application.add_handler(CommandHandler("today", followups_today))
    application.add_handler(CommandHandler("week", followups_week))
    application.add_handler(CommandHandler("overdue", followups_overdue))
    application.add_handler(CommandHandler("done", followup_done))

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
