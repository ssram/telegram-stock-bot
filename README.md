# Telegram Weinstein Stage Scanner

A GitHub-hosted stock tracking tool that:
- Runs Weinstein Stage Analysis on your holdings or watchlist, on demand
- Lets your team manage both lists via Telegram commands
- Stores all stock data in a Google Sheet
- Responds within seconds via an event-driven pipeline (Telegram webhook → Cloudflare Worker → GitHub Actions)

No server to maintain — GitHub Actions (free tier) + a free Cloudflare Worker.

For full architecture details, the complete command reference, and known
limitations, see **[WORKFLOW.md](./WORKFLOW.md)**. For fixing things when
they break, see **[TROUBLESHOOTING.md](./TROUBLESHOOTING.md)**. This file
covers setup only.

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
   The Watchlist tab is also auto-created the first time it's needed.

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

## 6. Deploy the Cloudflare Worker (event-driven pipeline)

This is what lets Telegram trigger GitHub Actions instantly instead of waiting on a schedule.

1. Go to [dash.cloudflare.com](https://dash.cloudflare.com) → sign up free → **Workers & Pages** → **Create Worker**.
2. Replace the default code with the contents of `cloudflare-worker/worker.js` from this repo → **Deploy**.
3. In the Worker's **Settings → Variables and Secrets**, add:

   | Secret | Value |
   |---|---|
   | `GITHUB_REPO` | `your-username/your-repo-name` — exactly this, no `https://`, no `/repos/` prefix |
   | `GITHUB_TOKEN` | A GitHub Personal Access Token with full **`repo`** scope (`public_repo` alone has been found insufficient) |
   | `TELEGRAM_WEBHOOK_SECRET` | Any random string — generate one with `-join ((48..57) + (65..90) + (97..122) \| Get-Random -Count 32 \| ForEach-Object {[char]$_})` in PowerShell. Save it in a password manager, you'll need it again below. |

4. Copy the Worker's URL (looks like `https://your-worker.your-subdomain.workers.dev`).
5. Register it as your bot's webhook, including the secret token so Telegram sends it back on every request:
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<YOUR_RANDOM_SECRET>
   ```
   You should see `{"ok":true,"result":true,"description":"Webhook was set"}`.

### Redeploying on a new machine

Both `worker.js` and `wrangler.toml` are committed in `cloudflare-worker/`,
so setting this up again (new machine, or after a hardware swap) is just:
```bash
cd cloudflare-worker
npm install -g wrangler
wrangler login
wrangler deploy
```
Then re-add the three secrets above from your password manager — no need
to rediscover any of the setup issues documented in `TROUBLESHOOTING.md`.

## 7. Try it out

In your Telegram group, send:

```
/help
/as RELIANCE 10 1350 1300 LongTerm
/ls
/ss
```

You should get a reply within a few seconds — check the repo's **Actions** tab
to see the corresponding run under **Telegram Command Handler**.

`/as` args are: `SYMBOL QUANTITY PRICE STOPLOSS INVESTTYPE`
(fullname, sector, and industry are auto-filled from Yahoo Finance; cmp and stage are filled in after a scan.)

See `WORKFLOW.md` for the full command list (holdings + watchlist), or
send `/helps` / `/helpw` in Telegram itself.

---

## File overview

| File | Purpose |
|---|---|
| `sheets.py` | Reads/writes the Google Sheet — holdings and watchlist |
| `telegram_bot.py` | Sends messages/files to Telegram |
| `weinstein_scanner.py` | Runs the Weinstein Stage Analysis over holdings or watchlist |
| `handle_command.py` | Processes one incoming Telegram command and dispatches to the right action |
| `formatting.py` | Builds Telegram-friendly monospaced tables for list/scan output |
| `cloudflare-worker/worker.js` | Receives Telegram's webhook, forwards it to GitHub |
| `.github/workflows/telegram_command.yml` | Runs instantly when a command arrives |
| `.github/workflows/scheduled_scan.yml` | Manual-only scan trigger (no automatic schedule) |

## Notes & limits

- **yfinance on GitHub Actions**: shared runner IPs occasionally get rate-limited by Yahoo Finance. If scans start failing, add a short `time.sleep()` between symbol lookups in `weinstein_scanner.py`.
- **Never commit real secrets** into any `.py` or `.js` file — always read them via `os.environ` (Python) or `env.*` (Worker), as already done throughout this codebase.
- If a secret is ever accidentally committed, revoke and regenerate it immediately rather than just deleting it from a later commit.
- For any pipeline issue — no reply, failed runs, dispatch errors — see **[TROUBLESHOOTING.md](./TROUBLESHOOTING.md)**, which covers debugging each hop of the pipeline plus credential rotation steps.
