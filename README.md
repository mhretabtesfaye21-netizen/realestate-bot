# Real Estate Client Follow-Up Bot

A Telegram bot to track clients, log notes, and never miss a follow-up.

## What it does

- Add clients with name, phone, and what they're interested in
- Log a running history of notes per client (calls, meetings, etc.)
- Schedule follow-ups (e.g. "call in 3 days")
- See what's due today, this week, or overdue
- Get an automatic daily reminder message every morning with your follow-up list

All data is stored locally in a file called `clients.db` — nothing goes to a third party.

## 1. Create your bot on Telegram

1. Open Telegram, search for **@BotFather**, and start a chat.
2. Send `/newbot` and follow the prompts (choose a name and a username ending in "bot").
3. BotFather will give you a **token** that looks like `123456789:AAExample...`. Copy it.

## 2. Install and run (on your own computer first)

You need Python 3.10+ installed. Then, in a terminal:

```bash
cd realestate_bot
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` in any text editor and paste your bot token:
```
BOT_TOKEN=123456789:AAExample...
```

Run it:
```bash
python bot.py
```

Now open Telegram, find your bot (by the username you gave it), and send `/start`.
It will reply with your **chat ID** — copy that number into `.env` as `OWNER_CHAT_ID`,
then restart the bot (Ctrl+C, then `python bot.py` again). This is what lets the bot
send you the daily reminder message automatically.

## 3. Try the commands

```
/addclient          - add a client (bot will ask name, phone, interest)
/clients             - list everyone
/find Ahmed          - search by name or phone
/view 1               - full details, notes, and follow-ups for client #1
/note 1 Called, wants viewing this weekend
/followup 1 3 Confirm viewing time     -> follow-up in 3 days
/today                - what's due today
/week                  - what's due in the next 7 days
/overdue              - what you missed
/done 1                - mark follow-up #1 as complete
```

## 4. Keep it running 24/7 (hosting)

Right now the bot only works while `python bot.py` is running on your computer.
To get reminders even when your laptop is off, you need to host it somewhere
that stays on all the time. A few beginner-friendly, low/no-cost options:

- **Railway.app** or **Render.com** — connect your code (e.g. via GitHub), they
  run it for you continuously. Both have free/cheap tiers and simple deploy
  instructions for Python apps. Just make sure to set `BOT_TOKEN` and
  `OWNER_CHAT_ID` as environment variables in their dashboard instead of a `.env` file.
- **A cheap VPS** (DigitalOcean, Hetzner, a low-cost local host) — more control,
  a few dollars a month, run the bot with something like `tmux` or a systemd
  service so it restarts if it crashes.
- **PythonAnywhere** — has a free tier, though "always-on" tasks may require a
  paid plan.

⚠️ One important note: `clients.db` is just a file next to `bot.py`. Whichever
host you choose, make sure its storage is *persistent* (not wiped on redeploy),
or your client list will disappear. If a host you're considering doesn't clearly
support persistent storage, ask me and I can check its docs with you.

## Notes on this version

- Built for a single salesperson (you) — everyone who messages the bot sees the
  same shared client list. If you later want multiple agents with separate
  client books, that's a straightforward extension (just say the word).
- Dates for follow-ups are entered as "days from now" (e.g. `3` = in 3 days) to
  keep things fast to type on mobile.
