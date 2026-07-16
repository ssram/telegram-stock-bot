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
| `stockName` | User (`/as`) | NSE symbol, unique key |
| `fullname` | Auto (yfinance) | Company name |
| `sector` | Auto (yfinance) | |
| `industry` | Auto (yfinance) | |
| `quantity` | User | |
| `price` | User | Buy price |
| `cmp` | Auto (`/ss`) | Live/current market price (via `fast_info`), updated on each scan — separate from the weekly data used for stage classification |
| `stoploss` | User | |
| `stage` | Auto (`/ss`) | Weinstein stage, updated on each scan |
| `Type` | User | e.g. LongTerm, Swing |
| `target` | User (`/ustgt`) | Target price, optional — used by `/lstg` |
| `status` | Auto (`/ss`) | `Entry` / `Hold` / `Exit` — see below for the rule. Default: `Hold` |
| `marketCap` | Auto (add time only) | `Large Cap` / `Mid Cap` / `Small Cap` / `Unknown` — approximate INR value bands (see below), **not** SEBI's official rank-based classification. Set once at `/as` time, not refreshed by `/ss` |
| `coreSatellite` | User (`/uscs`) | `Core` or `Satellite`, optional — a portfolio-construction label, not used by any automated logic |

**How `status` is chosen** (in `weinstein_scanner.py`'s `compute_status()`):
based on a mix of weekly signals (already computed for stage
classification) and **daily** signals (a separate fetch, just for this).
`status` no longer depends on `Type` at all — it's a pure technical rule.

Checked in this order — Exit first, then Entry, otherwise Hold:

| Status | Condition |
|---|---|
| `Exit` | **Any** of: (a) daily close below EMA10 for 2 consecutive days, (b) daily close below EMA21, (c) daily EMA10 < daily EMA21, (d) daily close below EMA50, (e) weekly close below the 30-week MA |
| `Entry` | **All** of (only checked if no Exit condition fired): (a) weekly 30W MA is rising, (b) daily close > daily EMA10, (c) daily EMA10 > daily EMA21, (d) daily EMA21 > daily EMA50 |
| `Hold` | Neither of the above — the default, and also the fallback if daily data couldn't be fetched |

This means `/ss` now fetches **two** separate yfinance datasets per
stock — weekly (for stage) and daily (for status) — roughly doubling
the API calls per stock compared to before. Worth watching if you have
a large holdings list (see Known Limitations).

**How `marketCap` is chosen** (in `sheets.py`'s `categorize_market_cap()`):
fetched once at `/as` time from yfinance's raw `marketCap` figure (in
INR), converted to crores and bucketed by value:

| Market cap (crore) | Category |
|---|---|
| >= 20,000 | `Large Cap` |
| 5,000 – 19,999 | `Mid Cap` |
| < 5,000 | `Small Cap` |
| missing/zero | `Unknown` |

This is an approximation using fixed value bands — it is **not** SEBI's
official classification, which ranks companies by relative market cap
(top 100 = large cap, 101-250 = mid cap, etc.) rather than a fixed
rupee threshold. Since yfinance doesn't expose that ranking, this is the
closest practical substitute. Not refreshed automatically — if a
company's market cap moves across a threshold later, the stored
category won't update on its own (would need to be manually corrected
or the stock re-added).

### Watchlist tab (auto-created on first use)

| Column | Filled by | Notes |
|---|---|---|
| `stockName` | User (`/aw`) | |
| `fullname` | Auto (yfinance) | |
| `sector` | Auto (yfinance) | |
| `industry` | Auto (yfinance) | |
| `cmp` | Auto (`/sw`) | |
| `stage` | Auto (`/sw`) | |
| `status` | Auto (`/sw`) | Same rule as Holdings — identical `compute_status()` call, since the rule never depended on `Type` |

No `quantity`/`price`/`stoploss`/`Type` on the Watchlist tab —
these aren't positions, just symbols being tracked.

---

## 4. Telegram commands

Each command has exactly one name (no aliases). All list/scan output
renders as a monospaced table (Telegram code block), sorted ascending by
symbol; stage-filtered lists show the matching count in the title (e.g.
"Holdings — Stage 2 (4)"). Access is restricted to user IDs listed in
`TELEGRAM_ALLOWED_USERS`. Scans (`/ss`, `/sw`) always post a summary
table; add `csv` as an argument (e.g. `/ss csv`) to also generate and
attach a downloadable CSV file — omitted by default to avoid writing a
file on every single scan. `/ls` and `/lw` also accept an optional `ss`
or `sw` argument respectively to refresh `cmp`/`stage` first, silently,
before listing — otherwise they just show whatever's currently stored,
which can be stale until the next scan. `cmp` itself is a live/current
quote (via `yfinance`'s `fast_info`), kept separate from the weekly
candle data used for stage classification — the two can legitimately
differ, especially mid-week before the current week's candle finalizes.

### Holdings

| Command | Usage | Purpose |
|---|---|---|
| `/as` | `/as SYMBOL QTY PRICE STOPLOSS TYPE` | Add a stock |
| `/ds` | `/ds SYMBOL` | Delete a stock |
| `/us` | `/us SYMBOL FIELD VALUE` | Update any field (`quantity`, `price`, `stoploss`, `Type`, `target`) |
| `/usqty` | `/usqty SYMBOL VALUE` | Update quantity only (sets it directly, overwriting) |
| `/usqtyadd` | `/usqtyadd SYMBOL VALUE` | Add VALUE to the existing quantity (e.g. topping up a position) |
| `/usqtysub` | `/usqtysub SYMBOL VALUE` | Subtract VALUE from the existing quantity (e.g. partial exit). Rejected if it would go negative — no change made in that case |
| `/usbuy` | `/usbuy SYMBOL VALUE` | Update buy price only |
| `/ussl` | `/ussl SYMBOL VALUE` | Update stoploss only |
| `/usit` | `/usit SYMBOL VALUE` | Update Type only |
| `/ustgt` | `/ustgt SYMBOL VALUE` | Update target price only |
| `/uscs` | `/uscs SYMBOL core\|satellite` | Set the Core/Satellite classification — only accepts exactly `core` or `satellite` (case-insensitive) |
| `/qssl` | `/qssl SYMBOL` | Query the current stoploss for a stock |
| `/lssl` | `/lssl [ss]` | List holdings where `cmp <= stoploss`. `ss` refreshes cmp/stage first |
| `/lstg` | `/lstg [ss]` | List holdings where `cmp > target` (skips stocks with no target set). `ss` refreshes cmp/stage first |
| `/it` | `/it [ss]` | List holdings whose `status` is not `Hold` (i.e. `Entry` or `Exit`), grouped by status. `ss` refreshes cmp/stage/status first |
| `/ss` | `/ss [csv]` | Scan holdings, write back `cmp`/`stage` for every symbol, post summary table (CSV optional) |
| `/ls` | `/ls [ss] [csv]` | List all holdings, ascending, as a table. `ss` refreshes cmp/stage first (silent scan); `csv` also attaches a full CSV |
| `/lsstg` | `/lsstg` | List holdings grouped by stage (one table per stage, each with a count) |
| `/lsstg2` | `/lsstg2` | List only Stage 2 holdings, with count |
| `/lsstg3` | `/lsstg3` | List only Stage 3 holdings, with count |
| `/lsstg4` | `/lsstg4` | List only Stage 4 holdings, with count *(note: no `/lsstg1` exists by design — holdings stage-filtering starts at Stage 2)* |

### Nifty signal

| Command | Usage | Purpose |
|---|---|---|
| `/nifty` | `/nifty` | Mechanical Nifty trend signal: 5-min EMA9/EMA21 crossover with price-action confirmation (candle must close beyond both EMAs). Suggests ATM strike + direction (CE/PE), with SL/target in **Nifty index points** — not option premium, since no live option-chain data source is integrated yet. See `nifty_signal.py` for the exact rule and constants (`SL_POINTS`, `TARGET_POINTS`, `STRIKE_INTERVAL`). This is a mechanical technical output, not financial advice. |

### Watchlist

| Command | Usage | Purpose |
|---|---|---|
| `/aw` | `/aw SYMBOL` | Add a symbol to the watchlist |
| `/dw` | `/dw SYMBOL` | Delete a symbol from the watchlist |
| `/sw` | `/sw [csv]` | Scan watchlist, write back `cmp`/`stage` for every symbol, post summary table (CSV optional) |
| `/lw` | `/lw [sw] [csv]` | List watchlist, ascending, as a table. `sw` refreshes cmp/stage first (silent scan); `csv` also attaches a full CSV |
| `/lwstg` | `/lwstg` | List watchlist grouped by stage (one table per stage, each with a count) |
| `/lwstg1` | `/lwstg1` | List only Stage 1 watchlist stocks, with count |
| `/lwstg2` | `/lwstg2` | List only Stage 2 watchlist stocks, with count |
| `/lwstg3` | `/lwstg3` | List only Stage 3 watchlist stocks, with count |
| `/lwstg4` | `/lwstg4` | List only Stage 4 watchlist stocks |

### Help

| Command | Purpose |
|---|---|
| `/help` | Compact overview of all commands, with command names highlighted |
| `/helps` | Holdings commands only, each with full usage |
| `/helpw` | Watchlist commands only, each with full usage |

Sending any command with missing required arguments returns a usage
message instead of failing silently (e.g. `/as` alone replies with
`` Usage: `/as SYMBOL QTY PRICE STOPLOSS INVESTTYPE` ``).

---

## 5. File reference

| File | Purpose |
|---|---|
| `sheets.py` | All Google Sheet reads/writes — holdings CRUD, watchlist CRUD, company-info lookup via yfinance |
| `telegram_bot.py` | Sends messages/documents to Telegram |
| `weinstein_scanner.py` | Core Weinstein Stage Analysis logic; `run_scan()` for holdings, `run_watchlist_scan()` for watchlist |
| `handle_command.py` | Processes a single incoming Telegram command, enforces the allowlist, dispatches to the right handler |
| `formatting.py` | Builds Telegram-friendly monospaced tables for list/scan output |
| `nifty_signal.py` | Mechanical Nifty EMA9/EMA21 trend signal and ATM strike suggestion (see below) |
| `cloudflare-worker/worker.js` | Verifies the webhook signature, forwards the command to GitHub via `repository_dispatch` |
| `cloudflare-worker/wrangler.toml` | Worker deployment config — lets the Worker be redeployed on a new machine with `wrangler deploy` |
| `.github/workflows/telegram_command.yml` | Runs `handle_command.py` the instant a command arrives |
| `.github/workflows/scheduled_scan.yml` | Manual-only holdings scan trigger |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Prevents accidental commits of local secrets/test files |
| `TROUBLESHOOTING.md` | Layer-by-layer debugging guide and credential rotation steps |

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
| `GITHUB_TOKEN` | A GitHub Personal Access Token with full `repo` scope, so the Worker can trigger the workflow |
| `TELEGRAM_WEBHOOK_SECRET` | Random string verified against Telegram's `X-Telegram-Bot-Api-Secret-Token` header, to reject forged requests |

---

## 6.5. Migrating an existing Sheet (adding/renaming columns)

If your Holdings tab was created before `target`/`status`/`marketCap`/
`coreSatellite` were added, add them manually: open the Sheet, and
after the last existing column, add these headers **in this exact
order** — `target`, `status`, `marketCap`, `coreSatellite` — exact
spelling and case matter, since they're compared against `sheets.py`'s
`COLUMNS` list. Existing rows can be left blank; `status` fills in on
the next `/ss`, `coreSatellite` via `/uscs` per stock. `marketCap` is
only ever set at `/as` (add) time — for stocks already in the sheet
before this column existed, it'll stay blank unless you fill it in
manually (there's no bulk-backfill command).
list. Existing rows can be left blank; they'll be filled in
automatically by the next `/ss` run (for `status`) or manually via
`/ustgt` (for `target`).

**If you previously had an `emaexit` column** (from an earlier version
of this bot): rename that header to `status` — the old EMA-label values
(`EMA10`/`EMA20`/`noworries`/etc.) will look stale until the next `/ss`
run overwrites them with real `Entry`/`Hold`/`Exit` values, which is
harmless, just cosmetically odd for one cycle.

Since the header-mismatch check is non-destructive (see Security below),
forgetting this step won't corrupt anything — it'll just print a warning
in the Actions log and these columns will have nothing to read/write
until they exist with the right names in the right order.

The Watchlist tab needs the same treatment — add/rename to `status`
after `stage`, if it was created before this was added.

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
the `command_text` input (e.g. `/ls`) → **Run workflow**.

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

## 9. Security

- **Sheet header safety**: `sheets.py` never clears/wipes the Sheet if
  its header row doesn't match the expected columns (e.g. after
  renaming a column). It only auto-creates the header on a genuinely
  empty sheet; any mismatch is logged as a warning in the Actions log
  instead of triggering a destructive `clear()`. This was previously a
  real risk — a header rename alone used to wipe all data.
- **Webhook authenticity**: the Worker verifies every request carries the
  correct `TELEGRAM_WEBHOOK_SECRET` (via Telegram's
  `X-Telegram-Bot-Api-Secret-Token` header) before forwarding anything to
  GitHub. Without this, anyone who found the Worker's URL could forge a
  request with a spoofed `user_id`, bypassing the Telegram-side allowlist
  entirely.
- **Token scope**: `GITHUB_TOKEN` uses full `repo` scope rather than the
  narrower `public_repo`, since the latter was found insufficient for the
  dispatches endpoint in practice (see `TROUBLESHOOTING.md`). This token
  can do more than trigger dispatches — treat it as sensitive, set an
  expiration, and rotate periodically.
- **Command allowlist**: `TELEGRAM_ALLOWED_USERS` restricts who can issue
  commands at all, checked in `handle_command.py` after the webhook
  signature check passes.
- **Credential rotation**: see `TROUBLESHOOTING.md` for step-by-step
  rotation instructions for every secret in this system (GitHub token,
  webhook secret, Telegram bot token, Google service account key).
- **Debugging**: see `TROUBLESHOOTING.md` for a layer-by-layer diagnostic
  guide covering the whole pipeline, plus real bugs hit during setup.

## 10. Known limitations

- **`/ss`/`/sw` fetch two datasets per stock now**: weekly (for stage)
  and daily (for `status`). This roughly doubles yfinance calls per
  stock — worth watching for rate-limiting on larger holdings lists.
- **Sender name display**: Telegram's `username` (public `@handle`) only
  exists if the sender has explicitly set one in their account. The
  Cloudflare Worker falls back to `first_name`, then the literal string
  `"unknown"`, in that order — so `"Scan requested by ..."` messages may
  show a first name rather than an `@handle` depending on the sender's
  Telegram settings. This is expected, not a bug.
- **Google Sheets write quota**: `/ss` and `/sw` batch every cell update
  for the entire scan into a single API call (`batch_update_holdings`/
  `batch_update_watchlist` in `sheets.py`), specifically to stay under
  Google's 60-writes/minute/user quota. If you ever add more per-stock
  write operations to the scan, keep using this batched pattern rather
  than calling `update_cell()` per field per stock — that's what caused
  a `429 Quota exceeded` error previously.
- **`/nifty` has no live option-premium data**: SL/target are in Nifty
  index points only. yfinance also doesn't reliably support NSE options
  contracts directly, so the actual option premium, its own SL/target,
  and Greeks aren't available yet — this is expected to improve once
  the planned Dhan API integration (or another options data feed) is
  wired in.
- **yfinance 5-minute data is limited to the last ~60 days** and is
  intraday-only — running `/nifty` outside market hours will show the
  last available candle (check the "as of" timestamp in the reply)
  rather than a truly live price.
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

## 11. Future: order placement (planned)

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
