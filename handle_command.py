"""
handle_command.py
===================
Handles ONE Telegram command per run. Triggered instantly by a
repository_dispatch event, fired by a Cloudflare Worker the moment
Telegram delivers a webhook message.

The command text and sender info arrive via environment variables, set
by the GitHub Actions workflow from the repository_dispatch payload:
  COMMAND_TEXT  - the raw message text, e.g. "/as RELIANCE 10 1350 1300 LongTerm"
  USER_ID       - numeric Telegram user ID of the sender
  USERNAME      - Telegram @username of the sender (for logging/messages)

Commands — Holdings
  /as SYMBOL QTY PRICE STOPLOSS INVESTTYPE   add stock
  /ds SYMBOL                                 delete stock
  /us SYMBOL FIELD VALUE                     update stock (FIELD: quantity|price|stoploss|investType)
  /usqty SYMBOL VALUE                        update quantity
  /usbuy SYMBOL VALUE                        update buy price
  /ussl SYMBOL VALUE                         update stoploss
  /usit SYMBOL VALUE                         update investment type
  /ss                                        scan stocks
  /ls                                        list stocks (ascending)
  /lsstg                                     list stage-wise (all stages, grouped)
  /lsstg2                                    list Stage 2 stocks
  /lsstg3                                    list Stage 3 stocks
  /lsst4                                     list Stage 4 stocks

Commands — Watchlist
  /aw SYMBOL       add stock to watchlist
  /dw SYMBOL       delete stock from watchlist
  /sw              scan stocks in watchlist
  /lw              list watchlist (ascending)
  /lwstg           list stage-wise (all stages, grouped)
  /lwstg1          list Stage 1 watchlist stocks
  /lwstg2          list Stage 2 watchlist stocks
  /lwstg3          list Stage 3 watchlist stocks
  /lwstg4          list Stage 4 watchlist stocks

Commands — Help
  /help    overall command list
  /helps   holdings commands only, with usage info
  /helpw   watchlist commands only, with usage info
"""

import os

from telegram_bot import send_message
from sheets import (
    add_stock,
    update_stock,
    remove_stock,
    get_all_holdings_records,
    get_holdings_by_stage,
    add_watchlist_stock,
    remove_watchlist_stock,
    get_all_watchlist_records,
    get_watchlist_by_stage,
)
from weinstein_scanner import run_scan, run_watchlist_scan
from formatting import build_holdings_table, build_watchlist_table, build_grouped_by_stage


# ---------------------------------------------------------------------------
# Holdings handlers
# ---------------------------------------------------------------------------

def handle_addstock(parts):
    if len(parts) < 2:
        return "Usage: `/as SYMBOL QTY PRICE STOPLOSS INVESTTYPE`"
    symbol = parts[1]
    quantity = float(parts[2]) if len(parts) > 2 else 0
    price = float(parts[3]) if len(parts) > 3 else 0
    stoploss = float(parts[4]) if len(parts) > 4 else 0
    invest_type = parts[5] if len(parts) > 5 else "Unknown"
    return add_stock(symbol, quantity, price, stoploss, invest_type)


def handle_removestock(parts):
    if len(parts) < 2:
        return "Usage: `/ds SYMBOL`"
    return remove_stock(parts[1])


def handle_updatestock(parts):
    if len(parts) < 4:
        return "Usage: `/us SYMBOL FIELD VALUE`\nFields: quantity, price, stoploss, investType"
    symbol, field, value = parts[1], parts[2], parts[3]
    return _apply_field_update(symbol, field, value)


def _apply_field_update(symbol, field, value):
    if field not in ("quantity", "price", "stoploss", "investType"):
        return f"⚠️ Invalid field '{field}'. Choose from: quantity, price, stoploss, investType"
    if field in ("quantity", "price", "stoploss"):
        try:
            value = float(value)
        except ValueError:
            return f"⚠️ {field} must be a number."
    return update_stock(symbol, **{field: value})


def _single_field_update(parts, command, field):
    if len(parts) < 3:
        return f"Usage: `{command} SYMBOL VALUE`"
    return _apply_field_update(parts[1], field, parts[2])


def handle_list_holdings():
    records = get_all_holdings_records()
    return build_holdings_table(records, title="Holdings")


def handle_list_holdings_by_stage():
    records = get_all_holdings_records()
    return build_grouped_by_stage(records, title_prefix="Holdings — Stage")


def handle_list_holdings_stage(stage_label):
    records = get_holdings_by_stage(stage_label)
    return build_holdings_table(records, title=f"Holdings — {stage_label}")


# ---------------------------------------------------------------------------
# Watchlist handlers
# ---------------------------------------------------------------------------

def handle_addwatchlist(parts):
    if len(parts) < 2:
        return "Usage: `/aw SYMBOL`"
    return add_watchlist_stock(parts[1])


def handle_removewatchlist(parts):
    if len(parts) < 2:
        return "Usage: `/dw SYMBOL`"
    return remove_watchlist_stock(parts[1])


def handle_list_watchlist():
    records = get_all_watchlist_records()
    return build_watchlist_table(records, title="Watchlist")


def handle_list_watchlist_by_stage():
    records = get_all_watchlist_records()
    return build_grouped_by_stage(records, title_prefix="Watchlist — Stage")


def handle_list_watchlist_stage(stage_label):
    records = get_watchlist_by_stage(stage_label)
    return build_watchlist_table(records, title=f"Watchlist — {stage_label}")


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "*Commands*\n\n"
    "`/as` `/ds` `/us` `/ss` `/ls` — holdings\n"
    "`/aw` `/dw` `/sw` `/lw` — watchlist\n"
    "`/lsstg` `/lwstg` — stage-wise lists\n\n"
    "`/helps` — holdings commands with usage\n"
    "`/helpw` — watchlist commands with usage\n"
    "`/help` — this message"
)

HELPS_TEXT = (
    "*Holdings commands*\n\n"
    "`/as SYMBOL QTY PRICE STOPLOSS INVESTTYPE` — add a stock\n"
    "`/ds SYMBOL` — delete a stock\n"
    "`/us SYMBOL FIELD VALUE` — update any field (FIELD: quantity, price, stoploss, investType)\n"
    "`/usqty SYMBOL VALUE` — update quantity\n"
    "`/usbuy SYMBOL VALUE` — update buy price\n"
    "`/ussl SYMBOL VALUE` — update stoploss\n"
    "`/usit SYMBOL VALUE` — update investment type\n"
    "`/ss` — scan holdings for Weinstein stage\n"
    "`/ls` — list all holdings (ascending)\n"
    "`/lsstg` — list holdings grouped by stage\n"
    "`/lsstg2` — list only Stage 2 holdings\n"
    "`/lsstg3` — list only Stage 3 holdings\n"
    "`/lsst4` — list only Stage 4 holdings"
)

HELPW_TEXT = (
    "*Watchlist commands*\n\n"
    "`/aw SYMBOL` — add a symbol to the watchlist\n"
    "`/dw SYMBOL` — delete a symbol from the watchlist\n"
    "`/sw` — scan watchlist for Weinstein stage\n"
    "`/lw` — list watchlist (ascending)\n"
    "`/lwstg` — list watchlist grouped by stage\n"
    "`/lwstg1` — list only Stage 1 watchlist stocks\n"
    "`/lwstg2` — list only Stage 2 watchlist stocks\n"
    "`/lwstg3` — list only Stage 3 watchlist stocks\n"
    "`/lwstg4` — list only Stage 4 watchlist stocks"
)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def get_allowed_users():
    raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    return set(int(uid.strip()) for uid in raw.split(",") if uid.strip())


def main():
    text = os.environ.get("COMMAND_TEXT", "").strip()
    user_id_raw = os.environ.get("USER_ID", "")
    username = os.environ.get("USERNAME", "unknown")

    if not text or not text.startswith("/"):
        print(f"Ignoring non-command text: {text!r}")
        return

    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        user_id = None

    allowed_users = get_allowed_users()
    if allowed_users and user_id not in allowed_users:
        send_message(f"⛔ Unauthorized user (@{username}, id={user_id}) tried: {text}")
        return

    parts = text.split()
    command = parts[0].lower()

    try:
        # --- Holdings ---
        if command == "/as":
            send_message(handle_addstock(parts))
        elif command == "/ds":
            send_message(handle_removestock(parts))
        elif command == "/us":
            send_message(handle_updatestock(parts))
        elif command == "/usqty":
            send_message(_single_field_update(parts, "/usqty", "quantity"))
        elif command == "/usbuy":
            send_message(_single_field_update(parts, "/usbuy", "price"))
        elif command == "/ussl":
            send_message(_single_field_update(parts, "/ussl", "stoploss"))
        elif command == "/usit":
            send_message(_single_field_update(parts, "/usit", "investType"))
        elif command == "/ss":
            send_message(f"🔍 Scan requested by @{username}, running...")
            run_scan(notify=True)
        elif command == "/ls":
            send_message(handle_list_holdings())
        elif command == "/lsstg":
            send_message(handle_list_holdings_by_stage())
        elif command == "/lsstg2":
            send_message(handle_list_holdings_stage("Stage 2"))
        elif command == "/lsstg3":
            send_message(handle_list_holdings_stage("Stage 3"))
        elif command == "/lsst4":
            send_message(handle_list_holdings_stage("Stage 4"))

        # --- Watchlist ---
        elif command == "/aw":
            send_message(handle_addwatchlist(parts))
        elif command == "/dw":
            send_message(handle_removewatchlist(parts))
        elif command == "/sw":
            send_message(f"🔍 Watchlist scan requested by @{username}, running...")
            run_watchlist_scan(notify=True)
        elif command == "/lw":
            send_message(handle_list_watchlist())
        elif command == "/lwstg":
            send_message(handle_list_watchlist_by_stage())
        elif command == "/lwstg1":
            send_message(handle_list_watchlist_stage("Stage 1"))
        elif command == "/lwstg2":
            send_message(handle_list_watchlist_stage("Stage 2"))
        elif command == "/lwstg3":
            send_message(handle_list_watchlist_stage("Stage 3"))
        elif command == "/lwstg4":
            send_message(handle_list_watchlist_stage("Stage 4"))

        # --- Help ---
        elif command == "/help":
            send_message(HELP_TEXT)
        elif command == "/helps":
            send_message(HELPS_TEXT)
        elif command == "/helpw":
            send_message(HELPW_TEXT)

        else:
            send_message(f"Unknown command: `{command}`\n\n{HELP_TEXT}")
    except Exception as e:
        send_message(f"⚠️ Error handling `{text}`: {e}")
        raise  # re-raise so the Actions run shows as failed in the log


if __name__ == "__main__":
    main()
