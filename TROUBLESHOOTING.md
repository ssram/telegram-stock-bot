# Troubleshooting Guide

The pipeline has four hops. When something breaks, work through them in
order — each check either confirms that hop works or points straight to
the fix.

```
Telegram  --webhook-->  Cloudflare Worker  --repository_dispatch-->  GitHub Actions  --reads/writes-->  Google Sheet
                                                                            |--sends reply-->  Telegram
```

---

## Step 1 — Is Telegram delivering to the Worker at all?

```
https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo
```

Check the `"last_error_message"` field in the response. If Telegram is
failing to reach the Worker, it shows up here directly (wrong URL,
Worker down, etc).

## Step 2 — What is the Worker actually doing?

```powershell
wrangler tail
```

Send a command in Telegram while this is running — this streams every
`console.log` and error from inside the Worker in real time. It's the
single most useful debugging tool for this pipeline.

| What you see | Meaning |
|---|---|
| Nothing at all | Telegram isn't reaching the Worker — back to Step 1 |
| `Rejected request: invalid or missing secret token` | Webhook secret mismatch between Telegram and the Worker (see "Rotating the webhook secret" below) |
| `GitHub dispatch failed: ...` | Worker reached GitHub, but GitHub rejected the request — go to Step 3 |
| No errors, but nothing happens in Telegram | Go to Step 4 |

## Step 3 — Is GitHub accepting the dispatch?

Test the exact same call outside the Worker, to isolate GitHub/token/repo
issues from Worker-code issues. Write the payload to a file first (more
reliable than inline JSON in PowerShell):

```powershell
'{"event_type":"telegram_command","client_payload":{"text":"/help"}}' | Out-File -FilePath payload.json -Encoding utf8

curl.exe -X POST -H "Authorization: Bearer YOUR_TOKEN" -H "Accept: application/vnd.github+json" -H "Content-Type: application/json" https://api.github.com/repos/YOUR_USERNAME/YOUR_REPO_NAME/dispatches -d "@payload.json"
```

| Response | Meaning |
|---|---|
| Empty response | Success — GitHub accepted it, check the Actions tab |
| `404 Not Found` | Wrong repo path, or token doesn't have write access to it |
| `400 Problems parsing JSON` | Malformed request body — use the `@payload.json` file approach above, not inline `-d "{...}"`, which PowerShell mangles |
| `401` / `403` | Token invalid, expired, or wrong scope |

## Step 4 — Did the GitHub Actions run actually succeed?

Repo → **Actions** tab → click the specific run → expand each step's logs.

| Symptom | Where to look |
|---|---|
| Run doesn't appear at all | Dispatch never arrived (back to Step 3), or `telegram_command.yml` isn't on the default branch |
| Run appears but fails | Read the Python traceback directly in the log |
| Run succeeds, no Sheet update | Check `GOOGLE_SHEET_NAME` / `GOOGLE_SERVICE_ACCOUNT_JSON` secrets, and that the Sheet is still shared with the service account email |
| Run succeeds, no Telegram reply | Check `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` secrets; `telegram_bot.py` catches send errors and only `print()`s them, so check the log output, not just for a traceback |

## Quick reference

| Symptom | Likely broken layer |
|---|---|
| No reply, no Actions run, `wrangler tail` shows nothing | Telegram → Worker (webhook registration or secret token) |
| No reply, no Actions run, `wrangler tail` shows an error | Worker → GitHub (token/repo/JSON) |
| Actions run appears but fails | GitHub Actions → Python code bug |
| Actions run succeeds, no Sheet change | Python → Google Sheets |
| Actions run succeeds, no Telegram reply | Python → Telegram |

---

## Real bugs hit during setup (worked examples)

These are documented in detail because they're easy to hit again after a
credential rotation or a fresh setup on a new machine.

**1. `public_repo` token scope returns 404 on `/dispatches`.**
Despite GitHub's own docs saying `public_repo` scope is sufficient for
dispatching to public repos, it did not work in practice here. Fix: use
the full `repo` scope instead (see "Generating a new GitHub token" below).

**2. PowerShell mangles inline JSON in curl's `-d` flag.**
`curl.exe -d "{\"key\":\"value\"}"` fails with `400 Problems parsing
JSON` on Windows PowerShell due to quote-escaping differences. Fix: write
the JSON to a file and use `-d "@payload.json"` instead (shown in Step 3
above).

**3. `GITHUB_REPO` secret containing a full URL instead of `owner/repo`.**
If the secret is accidentally set to something like
`https://api.github.com/repos/owner/repo` instead of just `owner/repo`,
the Worker's code (which builds `https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`)
produces a doubled, broken URL — resulting in a generic 404 (note: the
`documentation_url` in the error response is the generic
`https://docs.github.com/rest` rather than the specific dispatches page,
which is the tell that the URL itself is malformed, not just "repo not
found"). Fix: re-run `wrangler secret put GITHUB_REPO` and enter only
`owner/repo-name` — no protocol, no domain, no `/repos/`, no trailing
slash.

**4. `wrangler deploy` fails with "A permission error occurred while
accessing the file system."**
Usually means the terminal's working directory is a Windows-protected
system folder (e.g. `C:\WINDOWS\system32`) rather than the actual project
folder. Fix: `cd` into a folder under `C:\Users\YourUsername\` before
running any wrangler commands.

**5. `worker.js` deploys but Cloudflare errors "No event handlers were
registered."**
Means the file is empty or missing the `export default { fetch... }`
handler — commonly caused by Notepad silently saving as `worker.js.txt`.
Fix: write the file directly from PowerShell with `Out-File` instead of
Notepad, and verify with `type worker.js` before deploying.

---

## Rotating credentials

### Generating a new GitHub token (when it expires or is rotated)

1. GitHub → profile icon (top right) → **Settings**
2. **Developer settings** (bottom of left sidebar) → **Personal access
   tokens** → **Tokens (classic)**
3. **Generate new token** → **Generate new token (classic)**
4. Name it descriptively (e.g. `telegram-webhook-worker-2027`)
5. Set an expiration
6. Check the **top-level `repo`** checkbox (not just `public_repo` — see
   bug #1 above)
7. **Generate token** → copy it immediately, GitHub only shows it once

Test it works before updating the Worker:
```powershell
'{"event_type":"telegram_command","client_payload":{"text":"/help"}}' | Out-File -FilePath payload.json -Encoding utf8
curl.exe -X POST -H "Authorization: Bearer NEW_TOKEN" -H "Accept: application/vnd.github+json" -H "Content-Type: application/json" https://api.github.com/repos/YOUR_USERNAME/YOUR_REPO_NAME/dispatches -d "@payload.json"
```
Empty response = working. Then update the Worker:
```powershell
cd path\to\telegram-webhook-worker
wrangler secret put GITHUB_TOKEN
```
Paste the new token. No redeploy needed — secret updates apply
immediately to the live Worker.

Old token can be deleted from GitHub's token list once the new one is
confirmed working (Settings → Developer settings → Personal access
tokens → Tokens (classic) → find the old one → Delete).

### Rotating the webhook secret

If you ever suspect the `TELEGRAM_WEBHOOK_SECRET` has leaked, or just
want to rotate it periodically:

1. Generate a new random value:
   ```powershell
   -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
   ```
2. Update the Worker secret:
   ```powershell
   wrangler secret put TELEGRAM_WEBHOOK_SECRET
   ```
3. Re-register the webhook with Telegram, including the new secret:
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<NEW_SECRET>
   ```
4. Save the new value in your password manager, replacing the old entry.

### Rotating the Telegram bot token

Only needed if the bot token itself is suspected compromised:

1. Message **@BotFather** → `/mytoken` (to select the bot) → `/revoke`
2. BotFather issues a new token immediately
3. Update the GitHub secret:
   `Settings → Secrets and variables → Actions → TELEGRAM_BOT_TOKEN → Update`
4. Re-register the webhook (the URL and secret_token stay the same, but
   you're now calling `setWebhook` under the new bot token):
   ```
   https://api.telegram.org/bot<NEW_BOT_TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<YOUR_SECRET>
   ```

### Rotating the Google service account key

1. Google Cloud Console → **IAM & Admin → Service Accounts** → select
   the service account
2. **Keys** tab → **Add Key** → **Create new key** → JSON → download
3. Update the GitHub secret `GOOGLE_SERVICE_ACCOUNT_JSON` with the full
   contents of the new file
4. Delete the old key from the same **Keys** tab once the new one is
   confirmed working (test with a read command like `/ls` first)

---

## Account-level password/access recovery

These are for regaining access to the platforms themselves, not the
app's own secrets.

**GitHub account:**
- Forgot password: [github.com/password_reset](https://github.com/password_reset)
- Lost 2FA access: GitHub → sign-in page → "Having trouble?" →
  follow the account recovery flow (may require a waiting period if no
  recovery codes were saved)

**Cloudflare account:**
- Forgot password: [dash.cloudflare.com/login](https://dash.cloudflare.com/login) →
  "Forgot your password?"
- Signed in via Google: reset happens through your Google account
  instead — Cloudflare has no separate password to reset

**Google account (for Sheets access):**
- [accounts.google.com/signin/recovery](https://accounts.google.com/signin/recovery)
- If the *service account* JSON key is lost (not your personal Google
  login), that's not a password reset — generate a new key instead (see
  "Rotating the Google service account key" above)

**General good practice**: keep 2FA enabled on GitHub and Cloudflare, and
save account recovery codes somewhere durable (password manager) when
either platform offers them during setup.
