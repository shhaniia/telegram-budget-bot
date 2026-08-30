# Personal Budget Telegram Bot

A private Telegram bot for logging purchases, tracking budgets, alerting you
at spending thresholds, tracking recurring necessities, and reminding you
before subscriptions renew — so you can decide to keep or cancel them.

It's built to run as a single always-on background process (not a website),
talking to Telegram by polling. SQLite is the database — simple, and plenty
for one (or a couple of) people's spending history.

## Features

- **Log purchases** by just texting the bot (`12.50 lunch`), with `/log`, or
  by sending a **photo** of a receipt or an Apple Pay / Apple Wallet
  notification screenshot — OCR reads it and asks you to confirm before
  saving anything.
- **Categories**, with sensible defaults and `/addcategory` to add your own.
- **Budgets** per category, monthly or weekly, with a live `/budgets` view.
- **Threshold alerts** (default 80% and 100%) sent automatically the moment
  a logged purchase crosses them — configurable per category or globally.
- **Recurring purchases / necessities** (rent, groceries top-ups, insurance…)
  that auto-log on their due date, tagged separately so you can see
  necessity vs. discretionary spending in `/summary`.
- **Subscriptions**, with a reminder before each renewal (Keep / Cancel /
  Remind me tomorrow buttons) and automatic charge logging if you keep it.
- **Natural language.** You don't have to remember command names — "what
  did I spend today", "set a budget for food", "add a subscription" all
  route to the right command, and the bot tells you the command name so
  you can skip the round-trip next time (see `bot/intents.py`).
- **Daily logging reminder**, opt-in via `/remind on` — a nudge only on
  days you haven't logged anything, off by default.
- **Automatic budget pace check-ins**, sent unprompted mid-month and a few
  days before month-end: a per-category breakdown of how much of each
  monthly budget is used vs. how much of the month has elapsed, labeled
  over/under/within pace. A category running consistently off-pace for
  more than 3 check-ins in a row gets a one-tap "update this budget?"
  prompt. Also available on demand with `/checkin`.
- **Public by default.** Multiple people can use the same bot — every
  table (categories included) is scoped by Telegram `user_id`, so each
  person's categories, budgets, and history are completely private to
  them. Set `ALLOWED_USER_IDS` in `.env` to lock it back down to specific
  people; leave it empty and anyone on Telegram who finds the bot can use it.

## How the OCR (photo) logging works

Apple doesn't give third-party apps an API to pull Apple Wallet / Apple Pay
transaction history directly. So the flow here is: when you get a purchase
notification or open a transaction in Wallet, **screenshot it** and send the
screenshot to the bot (same for a physical receipt — just take a photo). The
bot runs OCR (Tesseract) on the image, guesses the amount, merchant and
category, and shows you a confirm screen before saving anything — you can
correct the amount with `/fix <id> <amount>` or pick a different category
before it's saved. OCR on real-world photos/screenshots is never perfect, so
that confirm step is deliberate, not a bug.

## Project layout

```
main.py                 entry point (run this)
bot/
  config.py              env var loading + validation
  db.py                  SQLite schema + all data access
  timeutil.py            timezone-aware date helpers
  ocr.py                 receipt/screenshot text extraction + parsing
  alerts.py              budget threshold alert logic
  jobs.py                daily job: recurring auto-log + subscription reminders/charges
  keyboards.py           inline keyboards
  handlers/               one module per command group
tests/
  test_logic.py           db/date/OCR-parsing smoke tests (no token/network needed)
  test_handlers.py         exercises the actual command handlers with mocked Telegram objects
Dockerfile, railway.json, Procfile   deployment
```

## 1. Create the bot on Telegram

1. Open a chat with **@BotFather** on Telegram.
2. Send `/newbot`, give it a name and a username (must end in `bot`).
3. BotFather gives you a token like `123456789:AAExample...` — save it, you'll
   need it as `TELEGRAM_BOT_TOKEN`.
4. Message **@userinfobot** to get your own numeric Telegram user ID — you'll
   need it as `ALLOWED_USER_IDS`. This bot refuses to respond to anyone whose
   ID isn't in that list, since it's handling your financial data.
5. Message your new bot once (e.g. `/start`) so Telegram lets it message you
   first for reminders — bots can only initiate a chat after the user has.

## 2. Configure

```
cp .env.example .env
```

Edit `.env` and fill in `TELEGRAM_BOT_TOKEN` at least. `ALLOWED_USER_IDS` is
optional — leave it empty (or unset) to run in public mode, where anyone on
Telegram can use the bot with their own private data; set it to a
comma-separated list of Telegram user IDs to restrict access to just those
people. `TIMEZONE` controls what "today/this week/this month" mean and what
local time the daily jobs (recurring/subscription processing, the pace
check-ins, and the opt-in `/remind` nudge) run at — set it to your own IANA
timezone (e.g. `Asia/Singapore`). `DAILY_CHECK_TIME` and `REMINDER_TIME`
default to `09:00` and `20:00` respectively and rarely need changing.

> This bot is designed for **private 1:1 chats only** (not group chats) —
> scheduled messages are sent to `chat_id == the recipient's user_id`, which
> only works for a direct message conversation with the bot.

## 3. Run it

### Locally

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# You also need the tesseract-ocr binary installed for photo logging:
#   macOS:  brew install tesseract
#   Ubuntu/Debian: sudo apt-get install tesseract-ocr
python main.py
```

The bot will start polling Telegram; open your chat with it and send
`/start`.

### With Docker

```
docker build -t budget-bot .
docker run -d --name budget-bot \
  --env-file .env \
  -v budget-bot-data:/data \
  budget-bot
```

The `-v budget-bot-data:/data` volume keeps your `budget.db` across
container restarts/rebuilds — without it you'll lose your data every time
you redeploy.

### Deploying to Railway (cloud, runs 24/7)

This repo includes a `Dockerfile` and `railway.json`, so Railway will build
and run it automatically.

1. Push this project to a GitHub repo (Railway deploys from GitHub, or you
   can use the Railway CLI to deploy a local folder directly).
2. On [railway.app](https://railway.app), create a new project → **Deploy
   from GitHub repo** (or `railway up` from the CLI in this folder).
3. In the service's **Variables** tab, add `TELEGRAM_BOT_TOKEN`,
   `ALLOWED_USER_IDS`, `TIMEZONE`, `DAILY_CHECK_TIME`. Leave `DATABASE_PATH`
   alone — the Dockerfile already points it at `/data/budget.db`.
4. In the service's **Volumes** tab, attach a volume mounted at `/data`.
   This is the important step — without it, your spending history is wiped
   every time Railway redeploys the service.
5. Deploy. No exposed port is needed — this is a background worker that
   polls Telegram, not a web server, so you can ignore Railway's "generate
   domain" prompt.

As of this writing, Railway's cheapest paid tier (Hobby, ~$5/month) includes
enough usage credit to comfortably run a small bot like this continuously,
and supports the volume this bot needs; there's also a one-time trial
credit for new accounts. Pricing/plans change over time — check
[Railway's pricing page](https://docs.railway.com/pricing/plans) for current
details. Render and Fly.io are alternatives that also work with this same
Dockerfile, but double-check they offer **persistent** storage on whatever
plan you pick — some free tiers don't, which would silently wipe your
spending history on every restart.

## 4. Using it

Send `/help` to the bot any time for the full command reference. Quick
start:

```
/setbudget Food 400
/setthreshold 80
12.50 lunch with the team
/budgets
/addrecurring Rent 1500 Housing monthly necessity
/addsub Netflix 15.99 2026-09-05 monthly 3
/summary
```

Or just send a photo of a receipt or an Apple Pay notification screenshot.

## Testing

No live bot token or network access is needed to run the test suite — it
mocks Telegram's objects and uses a temp SQLite file:

```
python tests/test_logic.py
python tests/test_handlers.py
python tests/test_wizard.py
python tests/test_multiuser.py
python tests/test_intents.py
python tests/test_checkins.py
```

## Notes / limitations

- **Single SQLite file.** Fine for personal use; if you ever need multiple
  people with independent budgets at real scale, you'd want to split
  storage per user or move to Postgres — the schema is already keyed by
  Telegram `user_id` throughout, so that's a moderate, not a rewrite-sized,
  change.
- **OCR accuracy** depends on photo quality/lighting — that's why every
  photo-logged purchase requires your confirmation before saving.
- **No bank API integration.** Apple Wallet/Apple Pay purchases go through
  the screenshot+OCR flow described above; there's no way to pull
  transaction history directly from Apple without an Apple Card CSV export,
  which isn't wired up here but would be a reasonable next feature if you
  have an Apple Card.
