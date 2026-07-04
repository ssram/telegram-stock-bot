# Telegram Weinstein Stage Scanner

A GitHub-hosted stock scanner that:
- Runs a daily Weinstein Stage Analysis scan on a schedule
- Lets your team manage the tracked stock list via Telegram commands
- Stores all stock data in a Google Sheet
- Posts results and confirmations back to a shared Telegram chat

No server required — everything runs on GitHub Actions' free tier.

---

## 1. Create the GitHub repo

1. Create a **public** repo (keeps GitHub Actions minutes unlimited and free).
   Secrets stay private regardless of repo visibility — see step 5.
2. Push all files in this project to the repo.

## 2. Create the Telegram bot

1. Message **@BotFather** on Telegram → `/newbot` → follow the prompts.
2. Save the token it gives you (looks like `123456789:AA...`) — this is `TELEGRAM_BOT_TOKEN`.
3. Create a group chat (if multiple people will use this) and add your bot to it.
4. Get the chat ID:
   - Send any message in the group.
   - Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser.
   - Find `"chat":{"id": ...}` in the response — that number (often negative for groups) is `TELEGRAM_CHAT_ID`.

## 3. Get each team member's Telegram user ID

Each person messages **@userinfobot** on Telegram — it replies with their numeric user ID.
Collect all IDs into a comma-separated list, e.g. `111111111,222222222`. This becomes `TELEGRAM_ALLOWED_USERS`.

## 4. Set up the Google Sheet

1. Create a Google Sheet (e.g. named `Stocks Master`).
2. Go to [Google Cloud Console](https://console.cloud.google.com/) → create a project.
3. Enable **Google Sheets API** and **Google Drive API**.
4. Create a **Service Account** (IAM & Admin → Service Accounts) → create a JSON key → download it.
5. Open the JSON file, find the `client_email` field (looks like `xxx@xxx.iam.gserviceaccount.com`).
6. Share your Google Sheet with that email address, with **Editor** access.
7. Leave the sheet's first tab empty — the code creates the header row automatically on first run.

## 5. Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**, and add:

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From step 2 |
| `TELEGRAM_CHAT_ID` | From step 2 |
| `TELEGRAM_ALLOWED_USERS` | From step 3 (comma-separated user IDs) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The **entire contents** of the downloaded JSON key file |
| `GOOGLE_SHEET_NAME` | The name of your Google Sheet, e.g. `Stocks Master` |

These are encrypted by GitHub and never appear in logs or code, even on a public repo.

## 6. Enable the workflows

Both workflows are already set up in `.github/workflows/`:
- `scheduled_scan.yml` — runs the scan daily at 9:00 AM IST on weekdays
- `telegram_listener.yml` — polls for new Telegram commands every 5 minutes during market hours (weekdays, 8am-9pm IST)

They'll start running automatically once pushed to GitHub, on the schedules defined.
You can also trigger either manually: repo → **Actions** tab → select the workflow → **Run workflow**.

## 7. Try it out

In your Telegram group, send:

```
/help
/addstock RELIANCE 10 1350 1300 LongTerm
/liststocks
/scan
```

`/addstock` args are: `SYMBOL QUANTITY PRICE STOPLOSS INVESTTYPE`
(fullname, sector, and industry are auto-filled from Yahoo Finance; cmp and stage are auto-filled on the next scan.)

---

## File overview

| File | Purpose |
|---|---|
| `sheets.py` | Reads/writes the Google Sheet (add, update, remove, list stocks) |
| `telegram_bot.py` | Sends messages/files to Telegram, polls for new messages |
| `weinstein_scanner.py` | Runs the Weinstein Stage Analysis over all tracked symbols |
| `listener.py` | Parses Telegram commands and dispatches to the right action |
| `.github/workflows/scheduled_scan.yml` | Daily automatic scan |
| `.github/workflows/telegram_listener.yml` | Polls for Telegram commands |
| `last_update_id.txt` | Tracks which Telegram messages have already been processed (auto-created) |

## Notes & limits

- **yfinance on GitHub Actions**: shared runner IPs occasionally get rate-limited by Yahoo Finance. If scans start failing, add a short `time.sleep()` between symbol lookups in `weinstein_scanner.py`.
- **Polling lag**: commands can take up to ~5 minutes to be picked up. If you need instant responses later, migrate `listener.py` to a webhook-based trigger (ask if you want this).
- **Never commit real secrets** into any `.py` file — always read them via `os.environ`, as already done throughout this codebase.
- If a secret is ever accidentally committed, revoke and regenerate it immediately (for the bot token: message @BotFather → `/revoke`) rather than just deleting it from a later commit.
