"""
listener.py
============
Polls Telegram for new commands (run on a short cron, e.g. every 5 min),
checks the sender against an allowlist, parses arguments, and dispatches
to the right action.

Commands:
  /addstock SYMBOL QTY PRICE STOPLOSS INVESTTYPE
  /updatestock SYMBOL FIELD VALUE       (FIELD: quantity|price|stoploss|investType)
  /removestock SYMBOL
  /liststocks
  /scan
  /help
"""

import os

from telegram_bot import get_updates, send_message
from sheets import add_stock, update_stock, remove_stock, list_stocks
from weinstein_scanner import run_scan

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
        return "Usage: /addstock SYMBOL QTY PRICE STOPLOSS INVESTTYPE"

    symbol = parts[1]
    quantity = float(parts[2]) if len(parts) > 2 else 0
    price = float(parts[3]) if len(parts) > 3 else 0
    stoploss = float(parts[4]) if len(parts) > 4 else 0
    invest_type = parts[5] if len(parts) > 5 else "Unknown"

    return add_stock(symbol, quantity, price, stoploss, invest_type)


def handle_updatestock(parts):
    # /updatestock SYMBOL FIELD VALUE
    if len(parts) < 4:
        return f"Usage: /updatestock SYMBOL FIELD VALUE\nFields: {', '.join(UPDATABLE_FIELDS)}"

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
        return "Usage: /removestock SYMBOL"
    return remove_stock(parts[1])


HELP_TEXT = (
    "*Commands*\n"
    "/addstock SYMBOL QTY PRICE STOPLOSS INVESTTYPE - Add a stock\n"
    "/updatestock SYMBOL FIELD VALUE - Update quantity, price, stoploss or investType\n"
    "/removestock SYMBOL - Remove a stock\n"
    "/liststocks - Show all tracked stocks\n"
    "/scan - Run Weinstein Stage 2 scan now\n"
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
            if command == "/addstock":
                send_message(handle_addstock(parts))
            elif command == "/updatestock":
                send_message(handle_updatestock(parts))
            elif command == "/removestock":
                send_message(handle_removestock(parts))
            elif command == "/liststocks":
                send_message(list_stocks())
            elif command == "/scan":
                send_message(f"🔍 Scan requested by @{username}, running...")
                run_scan(notify=True)
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
