# Telegram Weinstein Stage Scanner — Workflow Documentation

## 1. Overview

A GitHub-hosted stock tracking and analysis tool with no server to maintain.
It runs entirely on GitHub Actions' free tier, uses Google Sheets as its
database, and is controlled through Telegram commands.

**What it does:**
- Tracks two lists of stocks: **Holdings** (positions you actually hold)
  and **Watchlist** (symbols you're monitoring but don't hold).
- Runs Weinstein Stage Analysis (30-week SMA based stage classification)
  on either list, on demand.
- Lets a small team manage both lists via Telegram commands, with an
  allowlist restricting who can issue commands.
- Posts results, confirmations, and errors back to a shared Telegram chat.

---

## 2. Architecture

```
Telegram message
      |
      v
Telegram webhook (instant push)
      |
      v
Cloudflare Worker (receives webhook, forwards to GitHub)
      |
      v
GitHub repository_dispatch event
      |
      v
GitHub Actions: Telegram Command Handler   (runs within seconds)
      |
      +--> reads/writes -->  Google Sheet (Holdings tab + Watchlist tab)
      |
      +--> fetches prices --> Yahoo Finance (via yfinance)
      |
      +--> sends replies --> Telegram chat
```

Two independent GitHub Actions workflows exist:

| Workflow | Trigger | Purpose |
|---|---|---|
| `telegram_command.yml` | `repository_dispatch` (instant) + manual | Handles a single Telegram command the moment it arrives |
| `scheduled_scan.yml` | Manual only (`workflow_dispatch`) | Runs the holdings scan on demand; no longer runs automatically |

> Automatic daily scanning was intentionally removed — all scans (holdings
> or watchlist) now only happen when triggered by a `/scan`-type command
> in Telegram, or manually from the Actions tab.

### Why event-driven instead of polling

GitHub Actions has no persistent server to receive Telegram messages
directly, so a small piece of infrastructure sits in between: a
Cloudflare Worker (free tier) receives Telegram's webhook the instant a
message arrives, and immediately forwards it to GitHub via the
`repository_dispatch` API, which triggers `telegram_command.yml` within
seconds.

This replaced an earlier polling design (`telegram_listener.yml`, which
checked Telegram every 5 minutes via cron). Polling worked but added up
to ~5 minutes of lag per command; the event-driven design removes that
lag entirely, at the cost of one extra free Cloudflare account and a
one-time webhook registration step (see Setup below).

---

## 3. Data storage — Google Sheet

One spreadsheet, two tabs.

### Holdings tab (first/default tab)

| Column | Filled by | Notes |
|---|---|---|
| `stockName` | User (`/addst`) | NSE symbol, unique key |
| `fullname` | Auto (yfinance) | Company name |
| `sector` | Auto (yfinance) | |
| `industry` | Auto (yfinance) | |
| `quantity` | User | |
| `price` | User | Buy price |
| `cmp` | Auto (`/scnst`) | Current market price, updated on each scan |
| `stoploss` | User | |
| `stage` | Auto (`/scnst`) | Weinstein stage, updated on each scan |
| `investType` | User | e.g. LongTerm, Swing |

### Watchlist tab (auto-created on first use)

| Column | Filled by | Notes |
|---|---|---|
| `stockName` | User (`/addwl`) | |
| `fullname` | Auto (yfinance) | |
| `sector` | Auto (yfinance) | |
| `industry` | Auto (yfinance) | |
| `cmp` | Auto (`/scanwl`) | |
| `stage` | Auto (`/scanwl`) | |

No `quantity`/`price`/`stoploss`/`investType` on the Watchlist tab —
these aren't positions, just symbols being tracked.

---

## 4. Telegram commands

Every command has a short form and a long form; both work identically.
Access is restricted to user IDs listed in `TELEGRAM_ALLOWED_USERS`.

### Holdings

| Short | Long | Usage |
|---|---|---|
| `/addst` | `/addstock` | `/addst SYMBOL QTY PRICE STOPLOSS INVESTTYPE` |
| `/modst` | `/updatestock` | `/modst SYMBOL FIELD VALUE` — FIELD: `quantity`, `price`, `stoploss`, `investType` |
| `/delst` | `/removestock` | `/delst SYMBOL` |
| `/listst` | `/liststocks` | `/listst` — no arguments |
| `/scnst` | `/scan` | `/scnst` — runs Weinstein analysis on all holdings, writes back `cmp`/`stage`, posts summary + CSV |

### Watchlist

| Short | Long | Usage |
|---|---|---|
| `/addwl` | `/addwatchlist` | `/addwl SYMBOL` |
| `/delwl` | `/removewl`, `/removewatchlist` | `/delwl SYMBOL` |
| `/listwl` | `/listwatchlist` | `/listwl` — no arguments |
| `/scanwl` | `/scanwatchlist` | `/scanwl` — runs Weinstein analysis on watchlist, writes back `cmp`/`stage`, posts summary + CSV of Stage 2 hits |

### General

| Command | Purpose |
|---|---|
| `/help` | Shows the full command list with usage |

Sending any command with missing required arguments returns a usage
message instead of failing silently (e.g. `/addst` alone replies with
`Usage: /addst SYMBOL QTY PRICE STOPLOSS INVESTTYPE (or /addstock)`).

---

## 5. File reference

| File | Purpose |
|---|---|
| `sheets.py` | All Google Sheet reads/writes — holdings CRUD, watchlist CRUD, company-info lookup via yfinance |
| `telegram_bot.py` | Sends messages/documents to Telegram |
| `weinstein_scanner.py` | Core Weinstein Stage Analysis logic; `run_scan()` for holdings, `run_watchlist_scan()` for watchlist |
| `handle_command.py` | Processes a single incoming Telegram command, enforces the allowlist, dispatches to the right handler |
| `cloudflare-worker/worker.js` | Receives Telegram's webhook instantly, forwards the command to GitHub via `repository_dispatch` |
| `.github/workflows/telegram_command.yml` | Runs `handle_command.py` the instant a command arrives |
| `.github/workflows/scheduled_scan.yml` | Manual-only holdings scan trigger |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Prevents accidental commits of local secrets/test files |

---

## 6. GitHub Secrets required

Set under repo → **Settings → Secrets and variables → Actions**:

| Secret | Used for |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Authenticating with the Telegram Bot API |
| `TELEGRAM_CHAT_ID` | The shared chat where all replies/results are posted |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram user IDs allowed to issue commands |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full contents of the Google service account key, for Sheets/Drive API access |
| `GOOGLE_SHEET_NAME` | Name of the Google Sheet used as the database |

None of these ever appear in code — every value is read via `os.environ`
at runtime, which is what makes it safe to keep this repo **public**.

### Cloudflare Worker secrets (separate from GitHub)

Set under the Worker's **Settings → Variables and Secrets** in the
Cloudflare dashboard:

| Secret | Used for |
|---|---|
| `GITHUB_REPO` | e.g. `your-username/telegram-stock-bot` — tells the Worker which repo to dispatch to |
| `GITHUB_TOKEN` | A GitHub Personal Access Token with `repo` scope, so the Worker can trigger the workflow |

---

## 7. Setting up the event-driven pipeline (one-time)

1. **Deploy the Worker**: go to [dash.cloudflare.com](https://dash.cloudflare.com)
   (free account) → **Workers & Pages** → **Create Worker** → paste in the
   contents of `cloudflare-worker/worker.js` → **Deploy**.
2. **Add the Worker's secrets** (`GITHUB_REPO`, `GITHUB_TOKEN`) under its
   **Settings → Variables and Secrets**.
3. **Register the webhook** with Telegram — visit this URL once (browser
   or curl), replacing the placeholders:
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<WORKER_URL>
   ```
   A successful response looks like `{"ok":true,"result":true,"description":"Webhook was set"}`.
4. **Test it**: send `/help` in Telegram. A new run should appear in the
   repo's **Actions** tab under **Telegram Command Handler**, triggered by
   `repository_dispatch`, within a few seconds.

To confirm the webhook is correctly registered at any time:
```
https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo
```

To remove the webhook (e.g. to temporarily fall back to manual testing):
```
https://api.telegram.org/bot<BOT_TOKEN>/deleteWebhook
```

---

## 8. Testing changes safely

`repository_dispatch` always targets the **default branch** (`main`) —
unlike `schedule`, it has no concept of "run this on branch X." So to
test changes to `handle_command.py` or related files before merging:

**Option A — manual test via `workflow_dispatch` on a branch:**
```bash
git checkout -b test-branch-name
# make changes, commit, push
```
Then in the Actions tab: **Telegram Command Handler** → **Run workflow**
→ pick `test-branch-name` from the branch dropdown → enter a command in
the `command_text` input (e.g. `/liststocks`) → **Run workflow**.

Note: this bypasses the allowlist check (no real `USER_ID` is available
from a manual run), so it always executes — fine for testing logic, but
remember it still touches the real Sheet and real Telegram chat.

**Option B — merge to `main` and test live** once you're confident,
since real Telegram commands can only ever trigger the default branch's
code anyway.

Caveat either way: there's no sandboxed data — testing always writes to
the real Google Sheet and posts to the real Telegram chat, since both are
shared resources regardless of which branch or trigger ran the code.

---

## 9. Known limitations

- **yfinance on shared runners**: GitHub Actions runners share IPs, which
  can occasionally get rate-limited by Yahoo Finance. If scans start
  failing intermittently, adding a short delay between symbol lookups in
  `weinstein_scanner.py` is the usual fix.
- **No sandboxed test environment**: testing always touches the real
  Sheet and real Telegram chat (see above).
- **Cloudflare Worker is a second point of failure**: if the Worker goes
  down or its GitHub token expires, commands silently stop reaching
  GitHub. Worth periodically checking `getWebhookInfo` (see Setup) for
  delivery errors if commands seem to stop working.
- **GitHub token expiry**: personal access tokens can be set to expire;
  if the Worker suddenly stops forwarding commands, a stale/expired
  `GITHUB_TOKEN` secret in Cloudflare is the first thing to check.

---

## 10. Future: order placement (planned)

Dhan API integration is planned for placing real orders directly from
Telegram commands. Because this moves from read-only/data-entry actions
to real financial transactions, the following are planned safeguards
before this goes live:
- Separate read (scan) and write (order) code paths entirely.
- Two-step confirmation for any `/buy` or `/sell` command before it fires.
- Every order attempt logged (symbol, qty, side, timestamp, requester,
  broker order ID) to a dedicated Sheet tab.
- A stricter allowlist specifically for order-placing commands, separate
  from the general command allowlist.
- A `DRY_RUN` mode for testing the flow without placing real orders.
- Basic sanity limits (max order size, max orders/day) as a bug safety net.
