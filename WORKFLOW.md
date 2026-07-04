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
Telegram command
      |
      v
GitHub Actions: Telegram Command Listener   (polls every 5 min)
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
| `telegram_listener.yml` | Cron (every 5 min) + manual | Polls Telegram for new commands, dispatches to the right action |
| `scheduled_scan.yml` | Manual only (`workflow_dispatch`) | Runs the holdings scan on demand; no longer runs automatically |

> Automatic daily scanning was intentionally removed — all scans (holdings
> or watchlist) now only happen when triggered by a `/scan`-type command
> in Telegram, or manually from the Actions tab.

### Why polling instead of instant webhooks

GitHub Actions has no persistent server to receive Telegram messages in
real time, so `telegram_listener.yml` polls Telegram's `getUpdates` API on
a schedule instead. This means there's a **lag of up to ~5 minutes**
between sending a command and getting a response — acceptable for this
use case, since nothing here is time-critical to the second.

A webhook-based instant-response alternative (Telegram → Cloudflare
Worker → GitHub `repository_dispatch`) was designed but is currently on
hold; it removes the polling lag entirely at the cost of one extra free
Cloudflare account. See "Future: instant response" below if this becomes
worth revisiting.

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
| `telegram_bot.py` | Sends messages/documents to Telegram, polls for new updates |
| `weinstein_scanner.py` | Core Weinstein Stage Analysis logic; `run_scan()` for holdings, `run_watchlist_scan()` for watchlist |
| `listener.py` | Parses incoming Telegram commands, enforces the allowlist, dispatches to the right handler |
| `.github/workflows/telegram_listener.yml` | Polls for commands every 5 minutes |
| `.github/workflows/scheduled_scan.yml` | Manual-only holdings scan trigger |
| `last_update_id.txt` | Tracks which Telegram messages have already been processed (auto-committed by the listener workflow) |
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

---

## 7. Testing changes safely

Since all `workflow_dispatch`-triggered workflows let you pick a branch to
run from, changes can be tested without touching `main`:

```bash
git checkout -b test-branch-name
# make changes, commit, push
```
Then in the Actions tab: select the workflow → **Run workflow** → choose
`test-branch-name` from the branch dropdown.

Note: the **scheduled** trigger (`schedule:` cron) always runs whatever is
on the default branch (`main`), regardless of other branches — so testing
on a branch never interferes with production polling.

Caveat: there's no sandboxed data — a test run still writes to the real
Google Sheet and posts to the real Telegram chat, since both are shared
resources regardless of which branch triggered the run.

---

## 8. Known limitations

- **Polling lag**: up to ~5 minutes between sending a command and getting
  a response, since there's no instant webhook (see Architecture above).
- **yfinance on shared runners**: GitHub Actions runners share IPs, which
  can occasionally get rate-limited by Yahoo Finance. If scans start
  failing intermittently, adding a short delay between symbol lookups in
  `weinstein_scanner.py` is the usual fix.
- **No sandboxed test environment**: testing always touches the real
  Sheet and real Telegram chat (see above).

---

## 9. Future: instant response (on hold)

To remove the 5-minute polling lag entirely, replace `telegram_listener.yml`'s
cron trigger with an event-driven `repository_dispatch` trigger, fed by a
small free Cloudflare Worker that receives Telegram's webhook instantly
and forwards the command to GitHub. This was designed but deliberately
not deployed yet — revisit if response time becomes a real pain point.

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
