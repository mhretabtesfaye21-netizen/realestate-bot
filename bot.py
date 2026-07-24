"""
bot.py — Stage 1: Agencies, Workers, Owner approval

Three roles:
  OWNER   - you (OWNER_CHAT_ID in .env). Approves/revokes agencies.
  AGENCY  - a real estate company. Must be approved before anyone under it
            can use the bot. Registers with /register_agency <name>.
  WORKER  - a salesperson under one agency. Joins with /join <code>.
            Manages their own clients with red/yellow/green status.

No free trial. Nothing works until the owner approves the agency.

Owner-only commands:
  /pending                      - agencies waiting for approval
  /agencies                     - every agency and its status
  /approve_agency <id> <days>   - activate/extend an agency
  /revoke_agency <id>           - cut off an agency (and all its workers)
  /backup                       - download the database file

Agency-only commands:
  /workers          - list your workers and their join code
  /joincode          - show your join code again (to share with new workers)

Worker commands (require their agency to be active):
  /addclient, /clients, /find, /view, /note, /setstatus,
  /followup, /today, /week, /overdue, /done, /delete, /help
"""

import os
import random
import logging
from datetime import datetime, timedelta, time as dtime

from dotenv import load_dotenv
from telegram import (
    Update,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
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
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID")
REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR", "9"))
MONTHLY_PRICE = os.environ.get("MONTHLY_PRICE", "")
BANK_INFO = os.environ.get("BANK_INFO", "")
TELEBIRR_NUMBER = os.environ.get("TELEBIRR_NUMBER", "")

STATUS_EMOJI = {"red": "🔴", "yellow": "🟡", "green": "🟢"}


# Conversation states
NAME, PHONE, INTEREST = range(3)
REG_AGENCY_NAME, REG_WORKER_CODE = range(100, 102)
PROPERTY_PHOTO, PROPERTY_DESCRIPTION = range(200, 202)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    return "\n\n" + "\n".join(lines) + "\n\nAfter paying, contact the owner with your Telegram ID to get approved."


def fmt_client_line(row):
    dot = STATUS_EMOJI.get(row["status"], "⚪")
    return f"{dot} #{row['id']} {row['name']} — {row['phone'] or 'no phone'}"


def fmt_followup_line(row):
    dot = STATUS_EMOJI.get(row["client_status"], "⚪")
    return f"• #{row['id']} {row['due_date']} — {dot} {row['client_name']} ({row['client_phone'] or 'no phone'}): {row['note'] or ''}"


async def get_role(user_id):
    """Returns ('owner', None) / ('agency', row) / ('worker', row) / (None, None)."""
    if OWNER_CHAT_ID and str(user_id) == str(OWNER_CHAT_ID):
        return "owner", None
    agency = db.get_agency(user_id)
    if agency:
        return "agency", agency
    worker = db.get_worker(user_id)
    if worker:
        return "worker", worker
    return None, None


async def require_worker_access(update: Update):
    """Returns the worker row if allowed to use worker commands, else None
    (and sends an explanatory message)."""
    user = update.effective_user
    worker = db.get_worker(user.id)
    if worker is None:
        await update.message.reply_text(
            "You're not registered as a worker yet. Ask your agency for a join code, "
            "then send /join <code>."
        )
        return None
    if not db.worker_has_access(worker):
        await update.message.reply_text(
            "🚫 Your agency's access isn't active right now. Please check with your agency."
        )
        return None
    return worker


# ---------------------------------------------------------------------------
# /start and role registration
# ---------------------------------------------------------------------------

async def do_join(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    """Shared logic for joining an agency, used by both /join <code> and a
    tapped invite link (t.me/YourBot?start=CODE)."""
    user = update.effective_user
    agency = db.get_agency_by_join_code(code)
    if not agency:
        await update.message.reply_text("That join code doesn't match any agency. Double-check with them.")
        return
    db.register_worker(user.id, agency["telegram_id"], user.full_name)
    access = db.agency_has_access(agency)
    lang = agency["language"] or "en"
    await update.message.reply_text(
        f"🧑‍💼 You've joined '{agency['name']}'!\n"
        f"Access: {'✅ Active — you can start now.' if access else '🚫 Your agency is not yet active — check with them.'}",
        reply_markup=ReplyKeyboardRemove() if access else None,
    )
    if access:
        await send_quick_menu(update.effective_chat.id, context, "worker", lang)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    role, row = await get_role(user.id)

    # Tapped a link like t.me/YourBot?start=PAYLOAD
    if role is None and context.args:
        payload = context.args[0]
        if payload.startswith("browse-"):
            await start_browsing(update, context, payload[len("browse-"):])
        else:
            await do_join(update, context, payload)
        return

    if role == "owner":
        await update.message.reply_text(
            "👑 Welcome back, Owner! Type /approve_agency and /revoke_agency directly "
            "when you need to act on a specific ID.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await send_quick_menu(update.effective_chat.id, context, "owner")
        return

    if role == "agency":
        status_line = (
            f"Active until {row['subscription_end']}"
            if row["status"] == "active"
            else "Pending approval"
            if row["status"] == "pending"
            else "Revoked"
        )
        await update.message.reply_text(
            f"🏢 Welcome back, {row['name']}!\nStatus: {status_line}",
            reply_markup=ReplyKeyboardRemove(),
        )
        await send_quick_menu(update.effective_chat.id, context, "agency", row["language"] or "en")
        return

    if role == "worker":
        agency = db.get_agency(row["agency_id"])
        access = db.worker_has_access(row)
        lang = worker_language(row)
        await update.message.reply_text(
            f"🧑‍💼 Welcome back, {user.first_name}!\n"
            f"Agency: {agency['name'] if agency else 'unknown'}\n"
            f"Access: {'✅ Active' if access else '🚫 Not active'}",
            reply_markup=ReplyKeyboardRemove(),
        )
        if access:
            await send_quick_menu(update.effective_chat.id, context, "worker", lang)
        return

    # Brand new person - let them tap instead of typing a command
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏢 I'm an Agency", callback_data="role:agency")],
        [InlineKeyboardButton("🧑‍💼 I'm a Worker", callback_data="role:worker")],
    ])
    await update.message.reply_text(
        "👋 Welcome! This bot helps real estate agencies manage properties, "
        "workers, and clients.\n\nWhat are you?",
        reply_markup=keyboard,
    )


async def start_browsing(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str):
    """A client (their worker's customer) tapped their personal browse link.
    Shows them their agency's property listings with an 'interested' button."""
    client = db.get_client_by_token(token)
    if not client:
        await update.message.reply_text("This link isn't valid anymore. Please ask your agent for a new one.")
        return
    db.set_client_telegram_id(client["id"], update.effective_user.id)

    agency = db.get_agency(client["agency_id"])
    properties = db.list_properties(client["agency_id"])
    if not properties:
        await update.message.reply_text(
            f"🏠 Welcome! {agency['name'] if agency else 'Your agent'} hasn't posted any "
            "properties yet — check back soon!"
        )
        return

    await update.message.reply_text(
        f"🏠 Welcome! Here are {agency['name'] if agency else 'our'} available properties. "
        "Tap ❤️ on any you're interested in — your agent will be notified right away."
    )
    for p in properties:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("❤️ I'm interested", callback_data=f"interest:{p['id']}:{client['id']}")
        ]])
        if p["photo_file_id"]:
            await update.message.reply_photo(
                photo=p["photo_file_id"], caption=p["description"], reply_markup=keyboard
            )
        else:
            await update.message.reply_text(p["description"], reply_markup=keyboard)


async def finish_agency_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str):
    """Shared logic for registering an agency, used by both /register_agency
    and the tap-a-button conversational flow."""
    user = update.effective_user
    agency = db.register_agency(user.id, name)
    try:
        await context.bot.set_my_commands(
            AGENCY_COMMANDS, scope=BotCommandScopeChat(chat_id=user.id)
        )
    except Exception:
        logger.warning("Could not set agency command menu for %s", user.id)
    await update.message.reply_text(
        f"🏢 Agency '{name}' registered! Status: pending approval.\n"
        f"Your Telegram ID: {user.id}\n\n"
        "The owner needs to approve you before you can use the bot — "
        "they've been notified." + payment_instructions(),
        reply_markup=ReplyKeyboardRemove(),
    )
    if OWNER_CHAT_ID:
        try:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve 30 days", callback_data=f"approve:{user.id}:30"),
                    InlineKeyboardButton("✅ Approve 90 days", callback_data=f"approve:{user.id}:90"),
                ],
                [InlineKeyboardButton("❌ Reject", callback_data=f"reject:{user.id}")],
            ])
            await context.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=f"🆕 New agency wants approval: '{name}' (Telegram ID: {user.id})",
                reply_markup=keyboard,
            )
        except Exception:
            logger.warning("Could not notify owner of new agency signup.")


async def register_agency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Typed shortcut: /register_agency <name> in one message. Power-user path;
    most people will use the tap-a-button flow from /start instead."""
    user = update.effective_user
    role, _ = await get_role(user.id)
    if role is not None:
        await update.message.reply_text("You're already registered. Send /start to see your info.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /register_agency <your agency name>")
        return
    name = " ".join(context.args)
    await finish_agency_registration(update, context, name)


async def role_agency_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    role, _ = await get_role(user.id)
    if role is not None:
        await query.edit_message_text("You're already registered. Send /start to see your info.")
        return ConversationHandler.END
    await query.edit_message_text("🏢 Great! What's your agency (or company) name? Just type it and send.")
    return REG_AGENCY_NAME


async def reg_agency_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    await finish_agency_registration(update, context, name)
    return ConversationHandler.END


async def role_worker_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    role, _ = await get_role(user.id)
    if role is not None:
        await query.edit_message_text("You're already registered. Send /start to see your info.")
        return ConversationHandler.END
    await query.edit_message_text(
        "🧑‍💼 Okay! Type the join code your agency gave you and send it.\n"
        "(If they sent you a link instead, just tap that link directly — you don't need this.)"
    )
    return REG_WORKER_CODE


async def reg_worker_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    await do_join(update, context, code)
    return ConversationHandler.END


async def join_worker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    role, _ = await get_role(user.id)
    if role is not None:
        await update.message.reply_text("You're already registered. Send /start to see your info.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /join <code>\n(Get this code/link from your agency.)")
        return
    await do_join(update, context, context.args[0])


async def agency_invitelink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agency = db.get_agency(update.effective_user.id)
    if not agency:
        await update.message.reply_text("This command is for registered agencies only.")
        return
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={agency['join_code']}"
    await update.message.reply_text(
        f"📎 Send this link to your workers — they just tap it, nothing to type:\n\n{link}",
        reply_markup=ReplyKeyboardRemove(),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


HELP_TEXT = {
    "en": (
        "Here's what I can do:\n"
        "/addclient - add a new client\n"
        "/clients - list your clients (🔴🟡🟢 shows their status)\n"
        "/find <keyword> - search by name or phone\n"
        "/view <id> - see full client details (tap 🔴🟡🟢 there to update status)\n"
        "/note <id> <text> - add a note\n"
        "/followup <id> <days> <text> - schedule a follow-up\n"
        "/today - today's follow-ups\n"
        "/week - follow-ups in next 7 days\n"
        "/overdue - missed follow-ups\n"
        "/done <followup_id> - mark follow-up complete\n"
        "/delete <id> - remove a client\n"
        "/nextaction - who to contact next, and why\n"
        "/tips - short proven sales tips\n"
        "/leaderboard - see the team ranking\n"
        "/language - switch English/Amharic\n"
        "/help - show this list again anytime\n"
    ),
    "am": (
        "የምችለው ነገሮች ዝርዝር፦\n"
        "/addclient - አዲስ ደንበኛ ጨምር\n"
        "/clients - ደንበኞችህን ዘርዝር (🔴🟡🟢 ሁኔታቸውን ያሳያል)\n"
        "/find <ስም ወይም ስልክ> - ደንበኛ ፈልግ\n"
        "/view <id> - ሙሉ ዝርዝር ተመልከት (🔴🟡🟢 ተጭነው ሁኔታውን ይቀይሩ)\n"
        "/note <id> <ጽሑፍ> - ማስታወሻ ጨምር\n"
        "/followup <id> <ቀናት> <ጽሑፍ> - ክትትል ያዘጋጁ\n"
        "/today - ዛሬ የሚደረጉ ክትትሎች\n"
        "/week - በሚቀጥሉት 7 ቀናት ውስጥ ያሉ ክትትሎች\n"
        "/overdue - ያለፉ ክትትሎች\n"
        "/done <followup_id> - ክትትል እንደ ተጠናቀቀ ምልክት አድርግ\n"
        "/delete <id> - ደንበኛ አጥፋ\n"
        "/nextaction - ቀጥሎ ማን ማነጋገር እንዳለብዎ\n"
        "/tips - አጭር ውጤታማ ምክሮች\n"
        "/leaderboard - የቡድን ደረጃ ይመልከቱ\n"
        "/language - ቋንቋ ቀይር\n"
        "/help - ይህን ዝርዝር እንደገና አሳይ\n"
    ),
}

STATUS_LABEL = {
    "en": {"red": "not a buyer", "yellow": "in progress", "green": "bought"},
    "am": {"red": "ገዢ አይደለም", "yellow": "በሂደት ላይ", "green": "ገዝቷል"},
}


def worker_language(worker_row) -> str:
    if worker_row["language"]:
        return worker_row["language"]
    agency = db.get_agency(worker_row["agency_id"])
    return (agency["language"] if agency else "en") or "en"


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    lang = worker_language(worker)
    await update.message.reply_text(HELP_TEXT[lang], reply_markup=ReplyKeyboardRemove())


async def worker_language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    lang = worker_language(worker)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("English", callback_data="setlangworker:en"),
        InlineKeyboardButton("አማርኛ (Amharic)", callback_data="setlangworker:am"),
    ]])
    await update.message.reply_text(
        f"Current language: {lang}\nChoose your language:", reply_markup=keyboard
    )


# ---------------------------------------------------------------------------
# /addclient conversation (worker only)
# ---------------------------------------------------------------------------

async def addclient_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
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
    await update.message.reply_text("What are they interested in? (or - to skip)")
    return INTEREST


async def addclient_interest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    interest = "" if text == "-" else text
    worker = db.get_worker(update.effective_user.id)
    client_id = db.add_client(
        worker["telegram_id"], worker["agency_id"],
        context.user_data["name"], context.user_data["phone"], interest,
    )
    client = db.get_client(client_id, worker["telegram_id"])
    bot_username = (await context.bot.get_me()).username
    browse_link = f"https://t.me/{bot_username}?start=browse-{client['client_token']}"
    await update.message.reply_text(
        f"✅ Client added as #{client_id}: {context.user_data['name']} (starts as 🔴 red)\n\n"
        f"📎 Send them this link so they can browse our listings — tapping a property they "
        f"like notifies you instantly:\n{browse_link}\n\n"
        f"Tip: /followup {client_id} 3 First call"
    )
    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Clients: list / search / view / delete / status
# ---------------------------------------------------------------------------

async def clients_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    lang = worker_language(worker)
    rows = db.list_clients(worker["telegram_id"])
    if not rows:
        await update.message.reply_text(
            "No clients yet. Add one with /addclient", reply_markup=ReplyKeyboardRemove()
        )
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(fmt_client_line(r), callback_data=f"viewclient:{r['id']}")] for r in rows]
    )
    await update.message.reply_text("👥 Tap a client to see details:", reply_markup=keyboard)
    # Re-send the bottom menu right after, so it doesn't collapse behind "Menu"
    await update.message.reply_text("Use the buttons below 👇", reply_markup=ReplyKeyboardRemove())


async def clients_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    if not context.args:
        await update.message.reply_text("Usage: /find <name or phone>")
        return
    keyword = " ".join(context.args)
    rows = db.search_clients(worker["telegram_id"], keyword)
    if not rows:
        await update.message.reply_text("No matches found.")
        return
    lines = [fmt_client_line(r) for r in rows]
    await update.message.reply_text("🔎 Matches:\n" + "\n".join(lines))


async def render_client_detail(worker, client_id):
    """Builds the (text, keyboard) pair for a client's detail view. Returns
    (None, None) if the client doesn't exist / doesn't belong to this worker."""
    client = db.get_client(client_id, worker["telegram_id"])
    if not client:
        return None, None

    dot = STATUS_EMOJI.get(client["status"], "⚪")
    msg = (
        f"#{client['id']} — {client['name']} {dot}\n"
        f"Phone: {client['phone'] or '-'}\n"
        f"Interested in: {client['interest'] or '-'}\n\n"
    )

    followups = db.get_followups_for_client(client_id, worker["telegram_id"])
    if followups:
        msg += "📅 Upcoming follow-ups:\n"
        msg += "\n".join(f"  #{f['id']} {f['due_date']} — {f['note'] or ''}" for f in followups)
        msg += "\n\n"
    else:
        msg += "📅 No upcoming follow-ups.\n\n"

    notes = db.get_notes(client_id, worker["telegram_id"])
    if notes:
        msg += "📝 Notes (latest first):\n"
        msg += "\n".join(f"  [{n['created_at'][:16]}] {n['text']}" for n in notes[:10])
    else:
        msg += "📝 No notes yet."

    tip = random.choice(SALES_TIPS.get(client["status"], SALES_TIPS["general"]))
    msg += f"\n\n💡 Tip: {tip}"

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔴 Red", callback_data=f"setstatus:{client_id}:red"),
        InlineKeyboardButton("🟡 Yellow", callback_data=f"setstatus:{client_id}:yellow"),
        InlineKeyboardButton("🟢 Green", callback_data=f"setstatus:{client_id}:green"),
    ]])
    return msg, keyboard


async def client_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    if not context.args:
        await update.message.reply_text("Usage: /view <client_id>")
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return

    msg, keyboard = await render_client_detail(worker, client_id)
    if msg is None:
        await update.message.reply_text("Client not found.")
        return
    await update.message.reply_text(msg, reply_markup=keyboard)


async def client_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    if not context.args:
        await update.message.reply_text("Usage: /delete <client_id>")
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return
    client = db.get_client(client_id, worker["telegram_id"])
    if not client:
        await update.message.reply_text("Client not found.")
        return
    db.delete_client(client_id, worker["telegram_id"])
    await update.message.reply_text(f"🗑️ Deleted client #{client_id} ({client['name']}).")


async def client_set_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    if len(context.args) < 2 or context.args[1].lower() not in ("red", "yellow", "green"):
        await update.message.reply_text(
            "Usage: /setstatus <client_id> <red|yellow|green>\n"
            "🔴 red = not a buyer, 🟡 yellow = in progress, 🟢 green = bought"
        )
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return
    client = db.get_client(client_id, worker["telegram_id"])
    if not client:
        await update.message.reply_text("Client not found.")
        return
    status = context.args[1].lower()
    db.set_client_status(client_id, worker["telegram_id"], status)
    await update.message.reply_text(
        f"{STATUS_EMOJI[status]} {client['name']} is now marked {status}."
    )


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

async def note_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /note <client_id> <text>")
        return
    try:
        client_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Client id must be a number.")
        return
    client = db.get_client(client_id, worker["telegram_id"])
    if not client:
        await update.message.reply_text("Client not found.")
        return
    text = " ".join(context.args[1:])
    db.add_note(client_id, worker["telegram_id"], text)
    await update.message.reply_text(f"📝 Note added to {client['name']}.")


# ---------------------------------------------------------------------------
# Follow-ups
# ---------------------------------------------------------------------------

async def followup_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
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

    client = db.get_client(client_id, worker["telegram_id"])
    if not client:
        await update.message.reply_text("Client not found.")
        return

    note = " ".join(context.args[2:]) if len(context.args) > 2 else "Follow-up call"
    due_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    db.add_followup(client_id, worker["telegram_id"], due_date, note)
    await update.message.reply_text(f"📅 Follow-up scheduled for {client['name']} on {due_date}.")


async def followups_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    lang = worker_language(worker)
    today_str = datetime.now().strftime("%Y-%m-%d")
    rows = db.get_followups_due_on(worker["telegram_id"], today_str)
    if not rows:
        await update.message.reply_text("✅ No follow-ups due today.", reply_markup=ReplyKeyboardRemove())
        return
    lines = [fmt_followup_line(r) for r in rows]
    await update.message.reply_text(
        "📅 Today's follow-ups:\n" + "\n".join(lines), reply_markup=ReplyKeyboardRemove()
    )


async def followups_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    lang = worker_language(worker)
    today = datetime.now()
    end = today + timedelta(days=7)
    rows = db.get_followups_between(
        worker["telegram_id"], today.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    if not rows:
        await update.message.reply_text(
            "No follow-ups in the next 7 days.", reply_markup=ReplyKeyboardRemove()
        )
        return
    lines = [fmt_followup_line(r) for r in rows]
    await update.message.reply_text(
        "📅 Next 7 days:\n" + "\n".join(lines), reply_markup=ReplyKeyboardRemove()
    )


async def followups_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    lang = worker_language(worker)
    today_str = datetime.now().strftime("%Y-%m-%d")
    rows = db.get_overdue_followups(worker["telegram_id"], today_str)
    if not rows:
        await update.message.reply_text("🎉 Nothing overdue.", reply_markup=ReplyKeyboardRemove())
        return
    lines = [fmt_followup_line(r) for r in rows]
    await update.message.reply_text(
        "⚠️ Overdue follow-ups:\n" + "\n".join(lines), reply_markup=ReplyKeyboardRemove()
    )


async def followup_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    if not context.args:
        await update.message.reply_text("Usage: /done <followup_id>")
        return
    try:
        followup_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Follow-up id must be a number.")
        return
    followup = db.get_followup(followup_id, worker["telegram_id"])
    if not followup:
        await update.message.reply_text("Follow-up not found.")
        return
    db.mark_followup_done(followup_id, worker["telegram_id"])
    await update.message.reply_text(
        f"✅ Marked follow-up #{followup_id} as done.\n"
        f"Tip: schedule the next one with /followup {followup['client_id']} <days> <note>"
    )


# ---------------------------------------------------------------------------
# Agency-only commands
# ---------------------------------------------------------------------------

async def agency_workers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agency = db.get_agency(update.effective_user.id)
    if not agency:
        await update.message.reply_text("This command is for registered agencies only.")
        return
    lang = agency["language"] or "en"
    workers = db.list_workers_for_agency(agency["telegram_id"])
    if not workers:
        await update.message.reply_text(
            f"No workers yet. Share your join code with them: {agency['join_code']}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    lines = [f"• {w['name']} (ID: {w['telegram_id']})" for w in workers]
    await update.message.reply_text(
        "🧑‍💼 Your workers:\n" + "\n".join(lines), reply_markup=ReplyKeyboardRemove()
    )


async def agency_joincode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agency = db.get_agency(update.effective_user.id)
    if not agency:
        await update.message.reply_text("This command is for registered agencies only.")
        return
    await update.message.reply_text(
        f"Your join code: {agency['join_code']}\n"
        "Share this with your workers, or better, use /invitelink so they don't have to type anything."
    )


async def agency_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agency = db.get_agency(update.effective_user.id)
    if not agency:
        await update.message.reply_text("This command is for registered agencies only.")
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("English", callback_data="setlang:en"),
        InlineKeyboardButton("አማርኛ (Amharic)", callback_data="setlang:am"),
    ]])
    await update.message.reply_text(
        f"Current language: {agency['language']}\nChoose a language for your workers:",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Properties (agency posts photo + description; clients browse & tap interest)
# ---------------------------------------------------------------------------

async def addproperty_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agency = db.get_agency(update.effective_user.id)
    if not agency:
        await update.message.reply_text("This command is for registered agencies only.")
        return ConversationHandler.END
    if not db.agency_has_access(agency):
        await update.message.reply_text("Your agency's access isn't active right now.")
        return ConversationHandler.END
    await update.message.reply_text("📸 Send a photo of the property.")
    return PROPERTY_PHOTO


async def addproperty_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Please send a photo (not text). Or /cancel to stop.")
        return PROPERTY_PHOTO
    # Telegram sends multiple sizes - the last one is the largest
    context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    await update.message.reply_text(
        "Great! Now send a short description (price, location, rooms, etc.)."
    )
    return PROPERTY_DESCRIPTION


async def addproperty_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agency = db.get_agency(update.effective_user.id)
    description = update.message.text.strip()
    property_id = db.add_property(
        agency["telegram_id"], context.user_data.get("photo_file_id"), description
    )
    await update.message.reply_text(f"✅ Property #{property_id} posted!")
    context.user_data.clear()
    return ConversationHandler.END


async def list_properties(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agency = db.get_agency(update.effective_user.id)
    if not agency:
        await update.message.reply_text("This command is for registered agencies only.")
        return
    lang = agency["language"] or "en"
    props = db.list_properties(agency["telegram_id"])
    if not props:
        await update.message.reply_text(
            "No properties posted yet. Use /addproperty to add one.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    for p in props:
        caption = f"#{p['id']} — {p['description']}"
        if p["photo_file_id"]:
            await update.message.reply_photo(photo=p["photo_file_id"], caption=caption)
        else:
            await update.message.reply_text(caption)
    await update.message.reply_text("Use the buttons below 👇", reply_markup=ReplyKeyboardRemove())


async def list_interests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agency = db.get_agency(update.effective_user.id)
    if not agency:
        await update.message.reply_text("This command is for registered agencies only.")
        return
    lang = agency["language"] or "en"
    rows = db.list_interests_for_agency(agency["telegram_id"])
    if not rows:
        await update.message.reply_text("No client interest yet.", reply_markup=ReplyKeyboardRemove())
        return
    lines = [
        f"• {r['client_name']} → {r['property_description'][:40]} ({r['created_at'][:16]})"
        for r in rows
    ]
    await update.message.reply_text(
        "❤️ Client interest:\n" + "\n".join(lines), reply_markup=ReplyKeyboardRemove()
    )


# ---------------------------------------------------------------------------
# Leaderboard — ranks workers within an agency by clients marked 🟢 sold
# ---------------------------------------------------------------------------

def format_leaderboard(rows, highlight_id=None):
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i + 1}."
        marker = " ← you" if highlight_id is not None and r["telegram_id"] == highlight_id else ""
        lines.append(f"{medal} {r['name']} — {r['sold_count']} sold{marker}")
    return "\n".join(lines)


async def agency_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agency = db.get_agency(update.effective_user.id)
    if not agency:
        await update.message.reply_text("This command is for registered agencies only.")
        return
    rows = db.get_leaderboard(agency["telegram_id"])
    if not rows:
        await update.message.reply_text("No workers yet.", reply_markup=ReplyKeyboardRemove())
        return
    await update.message.reply_text(
        "🏆 Team Leaderboard:\n" + format_leaderboard(rows), reply_markup=ReplyKeyboardRemove()
    )


async def worker_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    rows = db.get_leaderboard(worker["agency_id"])
    if not rows:
        await update.message.reply_text("No leaderboard data yet.", reply_markup=ReplyKeyboardRemove())
        return
    await update.message.reply_text(
        "🏆 Leaderboard:\n" + format_leaderboard(rows, highlight_id=worker["telegram_id"]),
        reply_markup=ReplyKeyboardRemove(),
    )


# ---------------------------------------------------------------------------
# Next Best Action — tells a worker exactly who to contact next, and why
# ---------------------------------------------------------------------------

async def next_best_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    worker_id = worker["telegram_id"]
    today_str = datetime.now().strftime("%Y-%m-%d")

    overdue = db.get_overdue_followups(worker_id, today_str)
    if overdue:
        top = overdue[0]
        msg = (
            f"🎯 Contact next: {top['client_name']} {STATUS_EMOJI.get(top['client_status'], '')}\n"
            f"Why: their follow-up was due {top['due_date']} — they've been waiting the longest.\n"
            f"Suggested action: call now, apologize for the delay, and give a clear next step."
        )
        client_id_for_button = top["client_id"]
    else:
        stalled = db.get_stalled_yellow_clients(worker_id)
        if stalled:
            c = stalled[0]
            msg = (
                f"🎯 Contact next: {c['name']} 🟡\n"
                "Why: they're marked 'in progress' but have no follow-up scheduled — "
                "at risk of going cold.\n"
                "Suggested action: check in on their decision, then book a concrete next step."
            )
            client_id_for_button = c["id"]
        else:
            untouched = db.get_untouched_red_clients(worker_id)
            if untouched:
                c = untouched[0]
                msg = (
                    f"🎯 Contact next: {c['name']} 🔴\n"
                    "Why: added but never contacted yet — first impressions matter most early.\n"
                    "Suggested action: make first contact today, introduce yourself and ask what they're looking for."
                )
                client_id_for_button = c["id"]
            else:
                await update.message.reply_text(
                    "🎉 You're fully caught up! No urgent priorities right now.",
                    reply_markup=ReplyKeyboardRemove(),
                )
                return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("View this client", callback_data=f"viewclient:{client_id_for_button}")
    ]])
    await update.message.reply_text(msg, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Sales tips — short, practical, shown contextually by client status
# ---------------------------------------------------------------------------

SALES_TIPS = {
    "red": [
        "First contact works best within 24 hours — interest fades fast after that.",
        "Ask open questions first (budget, timeline, must-haves) before pitching anything.",
        "A short friendly voice call beats a long text message for a first impression.",
    ],
    "yellow": [
        "Give a specific next step every time you talk — 'I'll call Thursday' beats 'I'll follow up soon'.",
        "If they've gone quiet, a low-pressure check-in ('just thinking of you, any questions?') often restarts the conversation.",
        "Share something new each time — a fresh listing, a price update — not just 'checking in'.",
    ],
    "green": [
        "Ask for a referral now, while they're happiest with the deal.",
        "A short thank-you message after closing goes a long way for future business.",
    ],
    "general": [
        "Log a note right after every call — details fade fast, and it helps you sound prepared next time.",
        "Prioritize overdue follow-ups first thing each morning, before adding new leads.",
        "Consistency beats intensity — a client contacted every week beats one contacted intensely then dropped.",
    ],
}


async def sales_tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    worker = await require_worker_access(update)
    if not worker:
        return
    msg = "💡 Sales tips:\n\n"
    msg += "🔴 New leads:\n" + "\n".join(f"• {t}" for t in SALES_TIPS["red"]) + "\n\n"
    msg += "🟡 In progress:\n" + "\n".join(f"• {t}" for t in SALES_TIPS["yellow"]) + "\n\n"
    msg += "🟢 After closing:\n" + "\n".join(f"• {t}" for t in SALES_TIPS["green"]) + "\n\n"
    msg += "📌 General:\n" + "\n".join(f"• {t}" for t in SALES_TIPS["general"])
    await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())


# Owner-only commands
# ---------------------------------------------------------------------------

async def owner_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("This command is owner-only.")
        return
    rows = db.list_pending_agencies()
    if not rows:
        await update.message.reply_text(
            "No agencies waiting for approval.", reply_markup=ReplyKeyboardRemove()
        )
        return
    for r in rows:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve 30 days", callback_data=f"approve:{r['telegram_id']}:30"),
                InlineKeyboardButton("✅ Approve 90 days", callback_data=f"approve:{r['telegram_id']}:90"),
            ],
            [InlineKeyboardButton("❌ Reject", callback_data=f"reject:{r['telegram_id']}")],
        ])
        await update.message.reply_text(
            f"⏳ {r['name']} (ID: {r['telegram_id']})", reply_markup=keyboard
        )
    await update.message.reply_text("Use the buttons below 👇", reply_markup=ReplyKeyboardRemove())


async def owner_agencies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("This command is owner-only.")
        return
    rows = db.list_agencies()
    if not rows:
        await update.message.reply_text("No agencies registered yet.", reply_markup=ReplyKeyboardRemove())
        return
    lines = []
    for r in rows:
        detail = r["subscription_end"] if r["status"] == "active" else "-"
        lines.append(f"• {r['telegram_id']} — {r['name']} — {r['status']} (until {detail})")
    await update.message.reply_text(
        "🏢 Agencies:\n" + "\n".join(lines), reply_markup=ReplyKeyboardRemove()
    )


async def owner_approve_agency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("This command is owner-only.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /approve_agency <telegram_id> <days>")
        return
    try:
        telegram_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("telegram_id and days must be numbers.")
        return
    agency = db.get_agency(telegram_id)
    if not agency:
        await update.message.reply_text("No agency with that Telegram ID has registered yet.")
        return
    db.approve_agency(telegram_id, days)
    await update.message.reply_text(f"✅ Approved agency {telegram_id} for {days} days.")
    try:
        await context.bot.send_message(
            chat_id=telegram_id,
            text=(
                f"🎉 Your agency is now active for {days} days!\n"
                f"Your join code for workers: {agency['join_code']}\n"
                "Share /invitelink with your sales team."
            ),
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception:
        logger.warning("Could not notify agency %s of approval", telegram_id)


async def owner_revoke_agency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("This command is owner-only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /revoke_agency <telegram_id>")
        return
    try:
        telegram_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("telegram_id must be a number.")
        return
    db.revoke_agency(telegram_id)
    await update.message.reply_text(
        f"🚫 Revoked agency {telegram_id}. All their workers are now locked out too."
    )


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all inline button taps: owner approve/reject, and worker
    status changes / tapping a client from the list."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[0]

    # --- Owner-only actions ---
    if action in ("approve", "reject"):
        if not OWNER_CHAT_ID or str(query.from_user.id) != str(OWNER_CHAT_ID):
            await query.edit_message_text("This action is owner-only.")
            return

        if action == "approve":
            telegram_id, days = int(parts[1]), int(parts[2])
            agency = db.get_agency(telegram_id)
            if not agency:
                await query.edit_message_text("That agency no longer exists.")
                return
            db.approve_agency(telegram_id, days)
            await query.edit_message_text(f"✅ Approved '{agency['name']}' for {days} days.")
            try:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=(
                        f"🎉 Your agency is now active for {days} days!\n"
                        f"Your join code for workers: {agency['join_code']}\n"
                        "Share /invitelink with your sales team, or give them the code."
                    ),
                    reply_markup=ReplyKeyboardRemove(),
                )
            except Exception:
                logger.warning("Could not notify agency %s of approval", telegram_id)

        elif action == "reject":
            telegram_id = int(parts[1])
            agency = db.get_agency(telegram_id)
            if not agency:
                await query.edit_message_text("That agency no longer exists.")
                return
            db.revoke_agency(telegram_id)
            await query.edit_message_text(f"❌ Rejected '{agency['name']}'.")
            try:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text="Your agency registration was not approved. Contact the owner for details.",
                )
            except Exception:
                logger.warning("Could not notify agency %s of rejection", telegram_id)
        return

    # --- Client action: tapping "interested" on a property (public, no login) ---
    if action == "interest":
        property_id, client_id = int(parts[1]), int(parts[2])
        client = db.get_client_by_id(client_id)
        prop = db.get_property(property_id)
        if not client or not prop or prop["agency_id"] != client["agency_id"]:
            await query.edit_message_reply_markup(reply_markup=ReplyKeyboardRemove())
            return
        db.add_interest(client_id, property_id)
        try:
            if query.message.photo:
                await query.edit_message_caption(
                    caption=(query.message.caption or "") + "\n\n✅ You're interested — your agent will contact you soon!"
                )
            else:
                await query.edit_message_text(
                    (query.message.text or "") + "\n\n✅ You're interested — your agent will contact you soon!"
                )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=client["worker_id"],
                text=f"🔔 {client['name']} is interested in a property:\n{prop['description'][:200]}",
            )
        except Exception:
            logger.warning("Could not notify worker %s of client interest", client["worker_id"])
        return

    # --- Agency actions ---
    if action == "setlang":
        agency = db.get_agency(query.from_user.id)
        if not agency:
            await query.edit_message_text("This action is for registered agencies only.")
            return
        lang = parts[1]
        db.set_agency_language(agency["telegram_id"], lang)
        confirm = "✅ Language set to English." if lang == "en" else "✅ ቋንቋ ወደ አማርኛ ተቀይሯል።"
        await query.edit_message_text(confirm)
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="Use the buttons below 👇",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # --- Worker actions: require the tapper to be an active worker ---
    worker = db.get_worker(query.from_user.id)
    if not worker or not db.worker_has_access(worker):
        await query.edit_message_text("Your access isn't active right now.")
        return

    if action == "setlangworker":
        lang = parts[1]
        db.set_worker_language(worker["telegram_id"], lang)
        confirm = "✅ Language set to English." if lang == "en" else "✅ ቋንቋ ወደ አማርኛ ተቀይሯል።"
        await query.edit_message_text(confirm)
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="Use the buttons below 👇",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if action == "viewclient":
        client_id = int(parts[1])
        msg, keyboard = await render_client_detail(worker, client_id)
        if msg is None:
            await query.edit_message_text("Client not found.")
            return
        await query.edit_message_text(msg, reply_markup=keyboard)

    elif action == "setstatus":
        client_id, status = int(parts[1]), parts[2]
        client = db.get_client(client_id, worker["telegram_id"])
        if not client:
            await query.edit_message_text("Client not found.")
            return
        db.set_client_status(client_id, worker["telegram_id"], status)
        msg, keyboard = await render_client_detail(worker, client_id)
        await query.edit_message_text(
            f"{STATUS_EMOJI[status]} Status updated!\n\n{msg}", reply_markup=keyboard
        )


# ---------------------------------------------------------------------------
# Menu button router — dispatches taps on the persistent tap-menu to the
# existing command functions, so nobody has to type /commands for the
# common, no-typing-needed actions. (Defined further down, after every
# function it references already exists.)
# ---------------------------------------------------------------------------

async def send_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("This command is owner-only.")
        return
    if not os.path.exists(db.DB_PATH):
        await update.message.reply_text("No database file found yet.", reply_markup=ReplyKeyboardRemove())
        return
    try:
        with open(db.DB_PATH, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"backup_{datetime.now().strftime('%Y-%m-%d')}.db",
                caption="📦 Here's your current backup.",
            )
        await update.message.reply_text("Use the buttons below 👇", reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        logger.warning("Backup failed: %s", e)
        await update.message.reply_text(
            "Something went wrong sending the backup.", reply_markup=ReplyKeyboardRemove()
        )


# ---------------------------------------------------------------------------
# Daily reminder job — nudges every worker with something due today/overdue
# ---------------------------------------------------------------------------

async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE):
    today_str = datetime.now().strftime("%Y-%m-%d")

    for worker_id in db.all_worker_ids():
        worker = db.get_worker(worker_id)
        if not db.worker_has_access(worker):
            continue

        due_today = db.get_followups_due_on(worker_id, today_str)
        overdue = db.get_overdue_followups(worker_id, today_str)

        if not due_today and not overdue:
            continue

        msg = "☀️ Good morning! Don't forget:\n\n"
        if due_today:
            msg += "📅 Due today:\n" + "\n".join(fmt_followup_line(r) for r in due_today) + "\n\n"
        if overdue:
            msg += "⚠️ Overdue:\n" + "\n".join(fmt_followup_line(r) for r in overdue)

        try:
            await context.bot.send_message(chat_id=worker_id, text=msg)
        except Exception:
            logger.warning("Could not send daily reminder to worker %s", worker_id)


# ---------------------------------------------------------------------------
# Command menu (tappable ⌨️ button)
# ---------------------------------------------------------------------------

WORKER_COMMANDS = [
    BotCommand("addclient", "Add a new client"),
    BotCommand("clients", "List your clients"),
    BotCommand("find", "Search clients"),
    BotCommand("view", "View client details"),
    BotCommand("note", "Add a note to a client"),
    BotCommand("setstatus", "Set client status: red/yellow/green"),
    BotCommand("followup", "Schedule a follow-up"),
    BotCommand("today", "Today's follow-ups"),
    BotCommand("week", "Follow-ups in next 7 days"),
    BotCommand("overdue", "Missed follow-ups"),
    BotCommand("done", "Mark follow-up complete"),
    BotCommand("delete", "Delete a client"),
    BotCommand("language", "Switch English/Amharic"),
    BotCommand("help", "Show all commands"),
]

AGENCY_COMMANDS = [
    BotCommand("addproperty", "Post a new property"),
    BotCommand("properties", "See/manage your listings"),
    BotCommand("interests", "See who's interested in what"),
    BotCommand("invitelink", "Get a link for your workers to tap"),
    BotCommand("workers", "See your workers"),
    BotCommand("agencylanguage", "Switch English/Amharic"),
]

OWNER_COMMANDS = [
    BotCommand("pending", "Agencies awaiting approval"),
    BotCommand("agencies", "All agencies and their status"),
    BotCommand("approve_agency", "Approve/extend an agency"),
    BotCommand("revoke_agency", "Cut off an agency"),
    BotCommand("backup", "Download the database"),
]


async def setup_commands(application: Application):
    await application.bot.set_my_commands(WORKER_COMMANDS, scope=BotCommandScopeDefault())
    if OWNER_CHAT_ID:
        try:
            await application.bot.set_my_commands(
                OWNER_COMMANDS, scope=BotCommandScopeChat(chat_id=int(OWNER_CHAT_ID))
            )
        except Exception:
            logger.warning("Could not set owner command menu.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Quick menu — buttons attached directly under each message. These never
# collapse or need re-opening (unlike Telegram's bottom reply-keyboard row,
# which some clients hide behind a "Menu" toggle after every tap).
# ---------------------------------------------------------------------------

OWNER_ACTIONS_BY_KEY = {
    "pending": owner_pending,
    "agencies": owner_agencies,
    "backup": send_backup,
}
OWNER_QUICK_MENU_LABELS = {
    "pending": "⏳ Pending Agencies",
    "agencies": "🏢 All Agencies",
    "backup": "💾 Backup",
}

AGENCY_ACTIONS_BY_KEY = {
    "properties": list_properties,
    "interests": list_interests,
    "workers": agency_workers,
    "invitelink": agency_invitelink,
    "language": agency_language,
    "leaderboard": agency_leaderboard,
}
AGENCY_QUICK_MENU_LABELS = {
    "en": {
        "addproperty": "📸 Add Property", "properties": "🏠 My Properties", "interests": "❤️ Interests",
        "workers": "🧑‍💼 Workers", "invitelink": "🔗 Invite Link", "language": "🌐 Language",
        "leaderboard": "🏆 Leaderboard",
    },
    "am": {
        "addproperty": "📸 ንብረት ጨምር", "properties": "🏠 ንብረቶቼ", "interests": "❤️ ፍላጎቶች",
        "workers": "🧑‍💼 ሰራተኞች", "invitelink": "🔗 ሊንክ", "language": "🌐 ቋንቋ",
        "leaderboard": "🏆 ደረጃ",
    },
}

WORKER_ACTIONS_BY_KEY = {
    "clients": clients_list,
    "today": followups_today,
    "week": followups_week,
    "overdue": followups_overdue,
    "language": worker_language_command,
    "help": help_command,
    "nextaction": next_best_action,
    "tips": sales_tips,
    "leaderboard": worker_leaderboard,
}
WORKER_QUICK_MENU_LABELS = {
    "en": {
        "addclient": "➕ Add Client", "clients": "👥 My Clients", "nextaction": "🎯 Next Best Action",
        "today": "📅 Today", "week": "📆 This Week", "overdue": "⚠️ Overdue",
        "leaderboard": "🏆 Leaderboard", "tips": "💡 Sales Tips", "language": "🌐 Language", "help": "❓ Help",
    },
    "am": {
        "addclient": "➕ ደንበኛ ጨምር", "clients": "👥 ደንበኞቼ", "nextaction": "🎯 ቀጣይ ተግባር",
        "today": "📅 ዛሬ", "week": "📆 በዚህ ሳምንት", "overdue": "⚠️ ያለፉ",
        "leaderboard": "🏆 ደረጃ", "tips": "💡 ምክሮች", "language": "🌐 ቋንቋ", "help": "❓ እርዳታ",
    },
}


def quick_menu_inline(role: str, lang: str = "en") -> InlineKeyboardMarkup:
    """Buttons attached directly under a message — these never collapse or
    need re-opening, unlike the bottom reply-keyboard row."""
    if role == "owner":
        labels = OWNER_QUICK_MENU_LABELS
    elif role == "agency":
        labels = AGENCY_QUICK_MENU_LABELS.get(lang, AGENCY_QUICK_MENU_LABELS["en"])
    else:
        labels = WORKER_QUICK_MENU_LABELS.get(lang, WORKER_QUICK_MENU_LABELS["en"])

    buttons, row_buf = [], []
    for key, label in labels.items():
        row_buf.append(InlineKeyboardButton(label, callback_data=f"menuact:{role}:{key}"))
        if len(row_buf) == 2:
            buttons.append(row_buf)
            row_buf = []
    if row_buf:
        buttons.append(row_buf)
    return InlineKeyboardMarkup(buttons)


async def send_quick_menu(chat_id, context, role, lang="en"):
    await context.bot.send_message(
        chat_id=chat_id, text="What would you like to do?", reply_markup=quick_menu_inline(role, lang)
    )


async def menu_inline_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles taps on the never-collapsing inline quick-menu buttons."""
    query = update.callback_query
    await query.answer()
    _, role, key = query.data.split(":")

    # Small adapter so we can reuse the exact same functions the typed
    # commands use — they only need .effective_user and .message.reply_*
    adapter = type("Adapter", (), {"effective_user": query.from_user, "message": query.message})()

    if role == "owner" and is_owner(update) and key in OWNER_ACTIONS_BY_KEY:
        await OWNER_ACTIONS_BY_KEY[key](adapter, context)
        await send_quick_menu(query.message.chat_id, context, "owner")
        return

    if role == "agency":
        agency = db.get_agency(query.from_user.id)
        if agency and key in AGENCY_ACTIONS_BY_KEY:
            await AGENCY_ACTIONS_BY_KEY[key](adapter, context)
            await send_quick_menu(query.message.chat_id, context, "agency", agency["language"] or "en")
        return

    if role == "worker":
        worker = db.get_worker(query.from_user.id)
        if worker and db.worker_has_access(worker) and key in WORKER_ACTIONS_BY_KEY:
            await WORKER_ACTIONS_BY_KEY[key](adapter, context)
            await send_quick_menu(query.message.chat_id, context, "worker", worker_language(worker))
        return


async def addclient_start_from_quickmenu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    adapter = type("Adapter", (), {"effective_user": query.from_user, "message": query.message})()
    return await addclient_start(adapter, context)


async def addproperty_start_from_quickmenu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    adapter = type("Adapter", (), {"effective_user": query.from_user, "message": query.message})()
    return await addproperty_start(adapter, context)


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set. Fill in your .env file.")

    db.init_db()

    application = Application.builder().token(BOT_TOKEN).post_init(setup_commands).build()

    addclient_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addclient", addclient_start),
            CallbackQueryHandler(addclient_start_from_quickmenu, pattern="^menuact:worker:addclient$"),
        ],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addclient_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addclient_phone)],
            INTEREST: [MessageHandler(filters.TEXT & ~filters.COMMAND, addclient_interest)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    registration_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(role_agency_tap, pattern="^role:agency$"),
            CallbackQueryHandler(role_worker_tap, pattern="^role:worker$"),
        ],
        states={
            REG_AGENCY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_agency_name_received)],
            REG_WORKER_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_worker_code_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    addproperty_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addproperty", addproperty_start),
            CallbackQueryHandler(addproperty_start_from_quickmenu, pattern="^menuact:agency:addproperty$"),
        ],
        states={
            PROPERTY_PHOTO: [MessageHandler(filters.PHOTO, addproperty_photo)],
            PROPERTY_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, addproperty_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("register_agency", register_agency))
    application.add_handler(CommandHandler("join", join_worker))
    application.add_handler(addclient_conv)
    application.add_handler(registration_conv)
    application.add_handler(addproperty_conv)
    application.add_handler(CommandHandler("clients", clients_list))
    application.add_handler(CommandHandler("find", clients_find))
    application.add_handler(CommandHandler("view", client_view))
    application.add_handler(CommandHandler("delete", client_delete))
    application.add_handler(CommandHandler("setstatus", client_set_status))
    application.add_handler(CommandHandler("note", note_add))
    application.add_handler(CommandHandler("followup", followup_add))
    application.add_handler(CommandHandler("today", followups_today))
    application.add_handler(CommandHandler("week", followups_week))
    application.add_handler(CommandHandler("overdue", followups_overdue))
    application.add_handler(CommandHandler("done", followup_done))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("language", worker_language_command))
    application.add_handler(CommandHandler("nextaction", next_best_action))
    application.add_handler(CommandHandler("tips", sales_tips))
    application.add_handler(CommandHandler("leaderboard", worker_leaderboard))

    # Agency-only
    application.add_handler(CommandHandler("workers", agency_workers))
    application.add_handler(CommandHandler("joincode", agency_joincode))
    application.add_handler(CommandHandler("invitelink", agency_invitelink))
    application.add_handler(CommandHandler("agencylanguage", agency_language))
    application.add_handler(CommandHandler("properties", list_properties))
    application.add_handler(CommandHandler("interests", list_interests))
    application.add_handler(CommandHandler("agencyleaderboard", agency_leaderboard))

    # Owner-only
    application.add_handler(CommandHandler("pending", owner_pending))
    application.add_handler(CommandHandler("agencies", owner_agencies))
    application.add_handler(CommandHandler("approve_agency", owner_approve_agency))
    application.add_handler(CommandHandler("revoke_agency", owner_revoke_agency))
    application.add_handler(CommandHandler("backup", send_backup))
    application.add_handler(
        CallbackQueryHandler(
            callback_router,
            pattern=r"^(approve|reject|setlang|setlangworker|viewclient|setstatus|interest):",
        )
    )
    application.add_handler(CallbackQueryHandler(menu_inline_tap, pattern=r"^menuact:"))

    if application.job_queue:
        application.job_queue.run_daily(
            send_daily_reminders, time=dtime(hour=REMINDER_HOUR, minute=0)
        )
    else:
        logger.warning('JobQueue not available. Install with: pip install "python-telegram-bot[job-queue]"')

    logger.info("Bot starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
