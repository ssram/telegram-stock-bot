"""
listener.py
============
Polls Telegram for new commands (run on a short cron, e.g. every 5 min),
checks the sender against an allowlist, parses arguments, and dispatches
to the right action.

Commands (short form, long form still works too):
  /addst SYMBOL QTY PRICE STOPLOSS INVESTTYPE   (or /addstock)
  /modst SYMBOL FIELD VALUE                     (or /updatestock; FIELD: quantity|price|stoploss|investType)
  /delst SYMBOL                                 (or /removestock)
  /listst                                       (or /liststocks)
  /scnst                                        (or /scan)
  /addwl SYMBOL                                 (or /addwatchlist)
  /delwl SYMBOL                                 (or /removewl, /removewatchlist)
  /listwl                                       (or /listwatchlist)
  /scanwl                                       (or /scanwatchlist)
  /help
"""

import os

from telegram_bot import get_updates, send_message
from sheets import (
    add_stock,
    update_stock,
    remove_stock,
    list_stocks,
    add_watchlist_stock,
    remove_watchlist_stock,
    list_watchlist,
)
from weinstein_scanner import run_scan, run_watchlist_scan

OFFSET_FILE = "last_update_id.txt"

UPDATABLE_FIELDS = {"quantity", "price", "stoploss", "investType"}


def load_offset():
    if os.path.exists(OFFSET_FILE):
        content = open(OFFSET_FILE).read().strip()
        return int(content) if content else 0
    return 0


def save_offset(update_id):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(update_id))


def get_allowed_users():
    raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    return set(int(uid.strip()) for uid in raw.split(",") if uid.strip())


def handle_addstock(parts):
    # /addstock SYMBOL QTY PRICE STOPLOSS INVESTTYPE
    if len(parts) < 2:
        return "Usage: /addst SYMBOL QTY PRICE STOPLOSS INVESTTYPE (or /addstock)"

    symbol = parts[1]
    quantity = float(parts[2]) if len(parts) > 2 else 0
    price = float(parts[3]) if len(parts) > 3 else 0
    stoploss = float(parts[4]) if len(parts) > 4 else 0
    invest_type = parts[5] if len(parts) > 5 else "Unknown"

    return add_stock(symbol, quantity, price, stoploss, invest_type)


def handle_updatestock(parts):
    # /modst SYMBOL FIELD VALUE
    if len(parts) < 4:
        return f"Usage: /modst SYMBOL FIELD VALUE (or /updatestock)\nFields: {', '.join(UPDATABLE_FIELDS)}"

    symbol, field, value = parts[1], parts[2], parts[3]

    if field not in UPDATABLE_FIELDS:
        return f"⚠️ Invalid field '{field}'. Choose from: {', '.join(UPDATABLE_FIELDS)}"

    if field in ("quantity", "price", "stoploss"):
        try:
            value = float(value)
        except ValueError:
            return f"⚠️ {field} must be a number."

    return update_stock(symbol, **{field: value})


def handle_removestock(parts):
    if len(parts) < 2:
        return "Usage: /delst SYMBOL (or /removestock)"
    return remove_stock(parts[1])


def handle_addwatchlist(parts):
    if len(parts) < 2:
        return "Usage: /addwl SYMBOL (or /addwatchlist SYMBOL)"
    return add_watchlist_stock(parts[1])


def handle_removewatchlist(parts):
    if len(parts) < 2:
        return "Usage: /delwl SYMBOL (or /removewl, /removewatchlist)"
    return remove_watchlist_stock(parts[1])


HELP_TEXT = (
    "*Holdings*\n"
    "/addst SYMBOL QTY PRICE STOPLOSS INVESTTYPE - Add a stock (or /addstock)\n"
    "/modst SYMBOL FIELD VALUE - Update quantity, price, stoploss or investType (or /updatestock)\n"
    "/delst SYMBOL - Remove a stock (or /removestock)\n"
    "/listst - Show all tracked stocks (or /liststocks)\n"
    "/scnst - Run Weinstein Stage 2 scan on holdings (or /scan)\n\n"
    "*Watchlist*\n"
    "/addwl SYMBOL - Add a symbol to the watchlist (or /addwatchlist)\n"
    "/delwl SYMBOL - Remove a symbol from the watchlist (or /removewl, /removewatchlist)\n"
    "/listwl - Show the watchlist (or /listwatchlist)\n"
    "/scanwl - Run Weinstein Stage 2 scan on the watchlist (or /scanwatchlist)\n\n"
    "/help - Show this message"
)


def main():
    offset = load_offset()
    updates = get_updates(offset=offset + 1 if offset else None)
    allowed_users = get_allowed_users()

    for update in updates:
        offset = update["update_id"]
        message = update.get("message", {})
        text = message.get("text", "").strip()
        user = message.get("from", {})
        user_id = user.get("id")
        username = user.get("username", "unknown")

        if not text.startswith("/"):
            continue

        if allowed_users and user_id not in allowed_users:
            send_message(f"⛔ Unauthorized user (@{username}, id={user_id}) tried: {text}")
            continue

        parts = text.split()
        command = parts[0].lower()

        try:
            if command == "/addstock" or command == "/addst":
                send_message(handle_addstock(parts))
            elif command == "/updatestock" or command == "/modst":
                send_message(handle_updatestock(parts))
            elif command == "/removestock" or command == "/delst":
                send_message(handle_removestock(parts))
            elif command == "/liststocks" or command == "/listst":
                send_message(list_stocks())
            elif command == "/scan" or command == "/scnst":
                send_message(f"🔍 Scan requested by @{username}, running...")
                run_scan(notify=True)
            elif command == "/addwatchlist" or command == "/addwl":
                send_message(handle_addwatchlist(parts))
            elif command == "/removewatchlist" or command == "/removewl" or command == "/delwl":
                send_message(handle_removewatchlist(parts))
            elif command == "/listwatchlist" or command == "/listwl":
                send_message(list_watchlist())
            elif command == "/scanwatchlist" or command == "/scanwl":
                send_message(f"🔍 Watchlist scan requested by @{username}, running...")
                run_watchlist_scan(notify=True)
            elif command == "/help":
                send_message(HELP_TEXT)
            else:
                send_message(f"Unknown command: {command}\n\n{HELP_TEXT}")
        except Exception as e:
            send_message(f"⚠️ Error handling `{text}`: {e}")

    if offset:
        save_offset(offset)


if __name__ == "__main__":
    main()
