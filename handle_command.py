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
  /as SYMBOL QTY PRICE STOPLOSS TYPE   add stock
  /ds SYMBOL                           delete stock
  /us SYMBOL FIELD VALUE               update stock (FIELD: quantity|price|stoploss|Type)
  /usqty SYMBOL VALUE                  update quantity
  /usqtyadd SYMBOL VALUE               add VALUE to existing quantity
  /usqtysub SYMBOL VALUE               subtract VALUE from existing quantity
  /usbuy SYMBOL VALUE                  update buy price
  /ussl SYMBOL VALUE                   update stoploss
  /usit SYMBOL VALUE                   update Type (investment type)
  /ustgt SYMBOL VALUE                  update target price
  /uscs SYMBOL core|satellite          update Core/Satellite classification
  /qssl SYMBOL                         query stoploss for a stock
  /lssl [ss]                           list holdings where cmp <= stoploss
  /lstg [ss]                           list holdings where cmp > target
  /it [ss]                             list holdings with an Entry or Exit status (not Hold), grouped by status
  /nifty                               Nifty EMA9/EMA21 trend signal + ATM strike suggestion
  /ss [csv]                            scan stocks (csv attaches a downloadable file)
  /ls [ss] [csv]                       list stocks (ascending); ss=scan first, csv=attach file
  /lsstg                               list stage-wise (all stages, grouped)
  /lsstg2                              list Stage 2 stocks
  /lsstg3                              list Stage 3 stocks
  /lsstg4                              list Stage 4 stocks

Commands — Watchlist
  /aw SYMBOL       add stock to watchlist
  /dw SYMBOL       delete stock from watchlist
  /sw [csv]        scan stocks in watchlist (csv attaches a downloadable file)
  /lw [sw] [csv]   list watchlist (ascending); sw=scan first, csv=attach file
  /lwstg           list stage-wise (all stages, grouped)
  /lwstg1          list Stage 1 watchlist stocks
  /lwstg2          list Stage 2 watchlist stocks
  /lwstg3          list Stage 3 watchlist stocks
  /lwstg4          list Stage 4 watchlist stocks

Commands — Help
  /help    overall command list (tabular)
  /helps   holdings commands only, with usage info (tabular)
  /helpw   watchlist commands only, with usage info (tabular)
"""

import os
import pandas as pd

from telegram_bot import send_message, send_document
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
from formatting import build_holdings_table, build_watchlist_table, build_grouped_by_stage, build_grouped_by_type, build_table
from nifty_signal import get_nifty_signal, format_nifty_signal_message

HOLDINGS_LIST_CSV = "holdings_list.csv"
WATCHLIST_LIST_CSV = "watchlist_list.csv"

# Maps the field name a user types (case-insensitive) to the actual
# column header in the Sheet. Keeping this mapping separate from the
# Sheet's real column names means command syntax doesn't have to change
# if the Sheet's headers are ever renamed again.
FIELD_ALIASES = {
    "quantity": "quantity",
    "price": "price",
    "stoploss": "stoploss",
    "target": "target",
    "type": "Type",
    "investtype": "Type",  # kept for backward compatibility with older usage
    "cs": "coreSatellite",
    "coresatellite": "coreSatellite",
    # marketCap is deliberately NOT here — it's automatic-only, set once
    # at add time from yfinance, not user-editable via /us.
}


# ---------------------------------------------------------------------------
# Holdings handlers
# ---------------------------------------------------------------------------

def handle_addstock(parts):
    if len(parts) < 2:
        return "Usage: `/as SYMBOL QTY PRICE STOPLOSS TYPE`"
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
        return "Usage: `/us SYMBOL FIELD VALUE`\nFields: quantity, price, stoploss, Type"
    symbol, field, value = parts[1], parts[2], parts[3]
    return _apply_field_update(symbol, field, value)


def _apply_field_update(symbol, field, value):
    actual_column = FIELD_ALIASES.get(field.strip().lower())
    if actual_column is None:
        return f"⚠️ Invalid field '{field}'. Choose from: quantity, price, stoploss, Type"
    if actual_column in ("quantity", "price", "stoploss", "target"):
        try:
            value = float(value)
        except ValueError:
            return f"⚠️ {field} must be a number."
    return update_stock(symbol, **{actual_column: value})


def _single_field_update(parts, command, field):
    if len(parts) < 3:
        return f"Usage: `{command} SYMBOL VALUE`"
    return _apply_field_update(parts[1], field, parts[2])


def handle_update_core_satellite(parts):
    if len(parts) < 3:
        return "Usage: `/uscs SYMBOL core|satellite`"
    symbol = parts[1]
    value = parts[2].strip().lower()
    if value not in ("core", "satellite"):
        return "⚠️ Value must be exactly 'core' or 'satellite'."
    return update_stock(symbol, coreSatellite=value.capitalize())


def _adjust_quantity(parts, command, sign):
    """
    Shared logic for /usqtyadd (+) and /usqtysub (-): reads the stock's
    current quantity, applies the delta, and writes the new total back.
    Rejects (doesn't clamp) if the result would go negative — safer than
    silently flooring at zero, since that could hide a typo'd VALUE.
    """
    if len(parts) < 3:
        return f"Usage: `{command} SYMBOL VALUE`"

    symbol = parts[1].strip().upper()
    try:
        delta = float(parts[2])
    except ValueError:
        return "⚠️ VALUE must be a number."

    current = None
    for r in get_all_holdings_records():
        if str(r.get("stockName", "")).strip().upper() == symbol:
            current = r.get("quantity")
            break

    if current is None:
        return f"⚠️ {symbol} not found in holdings."

    try:
        current_qty = float(current) if current not in (None, "") else 0
    except (TypeError, ValueError):
        current_qty = 0

    new_qty = current_qty + (delta * sign)

    if new_qty < 0:
        if sign > 0:
            detail = f"Adding {delta} to current quantity {current_qty}"
        else:
            detail = f"Subtracting {delta} from current quantity {current_qty}"
        return f"⚠️ {detail} for {symbol} would go negative ({new_qty}). No change made."

    if new_qty == int(new_qty):
        new_qty = int(new_qty)

    return update_stock(symbol, quantity=new_qty)


def handle_qty_add(parts):
    return _adjust_quantity(parts, "/usqtyadd", +1)


def handle_qty_sub(parts):
    return _adjust_quantity(parts, "/usqtysub", -1)


def handle_query_stoploss(parts):
    if len(parts) < 2:
        return "Usage: `/qssl SYMBOL`"
    symbol = parts[1].strip().upper()
    for r in get_all_holdings_records():
        if str(r.get("stockName", "")).strip().upper() == symbol:
            sl = r.get("stoploss", "")
            sl_display = sl if sl not in (None, "") else "not set"
            return f"*{symbol}* stoploss: `{sl_display}`"
    return f"⚠️ {symbol} not found in holdings."


def _to_float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def handle_stoploss_hit(parts):
    """
    /lssl [ss]
    Lists holdings where cmp <= stoploss. ss refreshes cmp/stage first.
    Stocks missing a numeric cmp or stoploss are skipped (not applicable).
    """
    args = [p.lower() for p in parts[1:]]
    do_scan = "ss" in args

    if do_scan:
        send_message("🔄 Refreshing prices/stages first...")
        run_scan(notify=False, generate_csv=False)

    records = get_all_holdings_records()
    hits = []
    for r in records:
        cmp_val = _to_float_or_none(r.get("cmp"))
        sl_val = _to_float_or_none(r.get("stoploss"))
        if cmp_val is None or sl_val is None:
            continue
        if cmp_val <= sl_val:
            hits.append(r)

    hits.sort(key=lambda r: str(r.get("stockName", "")).upper())
    send_message(build_holdings_table(hits, title=f"Stoploss Hit ({len(hits)})"))


def handle_target_hit(parts):
    """
    /lstg [ss]
    Lists holdings where cmp > target. ss refreshes cmp/stage first.
    Stocks with no target set (blank, 0, or non-numeric) are skipped —
    there's nothing meaningful to compare against.
    """
    args = [p.lower() for p in parts[1:]]
    do_scan = "ss" in args

    if do_scan:
        send_message("🔄 Refreshing prices/stages first...")
        run_scan(notify=False, generate_csv=False)

    records = get_all_holdings_records()
    hits = []
    for r in records:
        cmp_val = _to_float_or_none(r.get("cmp"))
        target_val = _to_float_or_none(r.get("target"))
        if cmp_val is None or target_val is None or target_val <= 0:
            continue
        if cmp_val > target_val:
            hits.append(r)

    hits.sort(key=lambda r: str(r.get("stockName", "")).upper())
    send_message(build_holdings_table(hits, title=f"Target Hit ({len(hits)})"))


def handle_exit_alerts(parts):
    """
    /it [ss]
    Lists holdings whose status is NOT "Hold" (i.e. Entry or Exit — an
    actionable signal), grouped by status. ss refreshes
    cmp/stage/status first.
    """
    args = [p.lower() for p in parts[1:]]
    do_scan = "ss" in args

    if do_scan:
        send_message("🔄 Refreshing prices/stages first...")
        run_scan(notify=False, generate_csv=False)

    records = get_all_holdings_records()
    flagged = [
        r for r in records
        if str(r.get("status", "")).strip().lower() != "hold"
    ]

    if not flagged:
        send_message("✅ No entry/exit signals right now — everything's on Hold.")
        return

    flagged.sort(key=lambda r: str(r.get("stockName", "")).upper())
    send_message(build_grouped_by_type(flagged, type_field="status", title_prefix=""))


def handle_list_holdings(parts):
    """
    /ls [ss] [csv]
    ss  - run a silent scan first, so cmp/stage are fresh before listing
          (otherwise /ls just shows whatever is currently in the Sheet,
          which can be stale until the next /ss or scheduled scan).
    csv - also generate and attach a CSV of the full list.
    Sends its own messages/documents directly (not a plain string return)
    since it may need to send more than one thing.
    """
    args = [p.lower() for p in parts[1:]]
    do_scan = "ss" in args
    do_csv = "csv" in args

    if do_scan:
        send_message("🔄 Refreshing prices/stages first...")
        run_scan(notify=False, generate_csv=False)

    records = get_all_holdings_records()
    send_message(build_holdings_table(records, title="Holdings"))

    if do_csv:
        pd.DataFrame(records).to_csv(HOLDINGS_LIST_CSV, index=False)
        send_document(HOLDINGS_LIST_CSV, caption="Full holdings list")


def handle_list_holdings_by_stage():
    records = get_all_holdings_records()
    return build_grouped_by_stage(records, title_prefix="Holdings — Stage")


def handle_list_holdings_stage(stage_label):
    records = get_holdings_by_stage(stage_label)
    return build_holdings_table(records, title=f"Holdings — {stage_label} ({len(records)})")


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


def handle_list_watchlist(parts):
    """
    /lw [sw] [csv]
    sw  - run a silent watchlist scan first, so cmp/stage are fresh before
          listing.
    csv - also generate and attach a CSV of the full watchlist.
    """
    args = [p.lower() for p in parts[1:]]
    do_scan = "sw" in args
    do_csv = "csv" in args

    if do_scan:
        send_message("🔄 Refreshing prices/stages first...")
        run_watchlist_scan(notify=False, generate_csv=False)

    records = get_all_watchlist_records()
    send_message(build_watchlist_table(records, title="Watchlist"))

    if do_csv:
        pd.DataFrame(records).to_csv(WATCHLIST_LIST_CSV, index=False)
        send_document(WATCHLIST_LIST_CSV, caption="Full watchlist")


def handle_list_watchlist_by_stage():
    records = get_all_watchlist_records()
    return build_grouped_by_stage(records, title_prefix="Watchlist — Stage")


def handle_list_watchlist_stage(stage_label):
    records = get_watchlist_by_stage(stage_label)
    return build_watchlist_table(records, title=f"Watchlist — {stage_label} ({len(records)})")


# ---------------------------------------------------------------------------
# Help text — built as tables via formatting.build_table
# ---------------------------------------------------------------------------

def get_help_text():
    headers = ["COMMAND", "INFO"]
    rows = [
        ["/as /ds", "add / delete stock"],
        ["/us", "update stock field"],
        ["/uscs", "set core/satellite"],
        ["/qssl", "query stoploss"],
        ["/lssl /lstg", "stoploss / target hits"],
        ["/it", "entry/exit status"],
        ["/nifty", "nifty trend signal"],
        ["/ss /ls", "scan / list holdings"],
        ["/aw /dw", "add / delete watch"],
        ["/sw /lw", "scan / list watch"],
        ["/lsstg", "holdings by stage"],
        ["/lwstg", "watch by stage"],
        ["/helps", "holdings help"],
        ["/helpw", "watchlist help"],
    ]
    return "*Commands*\n" + build_table(headers, rows, max_col_width=None)


def get_helps_text():
    headers = ["COMMAND", "ARGS", "DESCRIPTION"]
    rows = [
        ["/as", "SYMBOL QTY PRICE SL TYPE", "add a stock"],
        ["/ds", "SYMBOL", "delete a stock"],
        ["/us", "SYMBOL FIELD VALUE", "update any field"],
        ["/usqty", "SYMBOL VALUE", "update quantity"],
        ["/usqtyadd", "SYMBOL VALUE", "add VALUE to existing quantity"],
        ["/usqtysub", "SYMBOL VALUE", "subtract VALUE from existing quantity"],
        ["/usbuy", "SYMBOL VALUE", "update buy price"],
        ["/ussl", "SYMBOL VALUE", "update stoploss"],
        ["/usit", "SYMBOL VALUE", "update Type"],
        ["/ustgt", "SYMBOL VALUE", "update target price"],
        ["/uscs", "SYMBOL core|satellite", "set Core/Satellite classification"],
        ["/qssl", "SYMBOL", "query stoploss"],
        ["/lssl", "[ss]", "list stocks where cmp <= stoploss"],
        ["/lstg", "[ss]", "list stocks where cmp > target"],
        ["/it", "[ss]", "list Entry/Exit stocks (not Hold), grouped by status"],
        ["/nifty", "-", "Nifty EMA9/EMA21 trend + ATM strike suggestion"],
        ["/ss", "[csv]", "scan holdings"],
        ["/ls", "[ss] [csv]", "list holdings (asc)"],
        ["/lsstg", "-", "list grouped by stage"],
        ["/lsstg2", "-", "list Stage 2 only"],
        ["/lsstg3", "-", "list Stage 3 only"],
        ["/lsstg4", "-", "list Stage 4 only"],
    ]
    return "*Holdings commands*\n" + build_table(headers, rows, max_col_width=None)


def get_helpw_text():
    headers = ["COMMAND", "ARGS", "DESCRIPTION"]
    rows = [
        ["/aw", "SYMBOL", "add to watchlist"],
        ["/dw", "SYMBOL", "delete from watchlist"],
        ["/sw", "[csv]", "scan watchlist"],
        ["/lw", "[sw] [csv]", "list watchlist (asc)"],
        ["/lwstg", "-", "list grouped by stage"],
        ["/lwstg1", "-", "list Stage 1 only"],
        ["/lwstg2", "-", "list Stage 2 only"],
        ["/lwstg3", "-", "list Stage 3 only"],
        ["/lwstg4", "-", "list Stage 4 only"],
    ]
    return "*Watchlist commands*\n" + build_table(headers, rows, max_col_width=None)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def get_allowed_users():
    raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    return set(int(uid.strip()) for uid in raw.split(",") if uid.strip())


def _wants_csv(parts):
    """Checks for an optional trailing 'csv' argument, e.g. `/ss csv`."""
    return len(parts) > 1 and parts[1].strip().lower() == "csv"


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
        send_message(f"⛔ Unauthorized user ({username}, id={user_id}) tried: {text}")
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
        elif command == "/usqtyadd":
            send_message(handle_qty_add(parts))
        elif command == "/usqtysub":
            send_message(handle_qty_sub(parts))
        elif command == "/usbuy":
            send_message(_single_field_update(parts, "/usbuy", "price"))
        elif command == "/ussl":
            send_message(_single_field_update(parts, "/ussl", "stoploss"))
        elif command == "/usit":
            send_message(_single_field_update(parts, "/usit", "Type"))
        elif command == "/ustgt":
            send_message(_single_field_update(parts, "/ustgt", "target"))
        elif command == "/uscs":
            send_message(handle_update_core_satellite(parts))
        elif command == "/qssl":
            send_message(handle_query_stoploss(parts))
        elif command == "/lssl":
            handle_stoploss_hit(parts)
        elif command == "/lstg":
            handle_target_hit(parts)
        elif command == "/it":
            handle_exit_alerts(parts)
        elif command == "/nifty":
            send_message(format_nifty_signal_message(get_nifty_signal()))
        elif command == "/ss":
            send_message(f"🔍 Scan requested by {username}, running...")
            run_scan(notify=True, generate_csv=_wants_csv(parts))
        elif command == "/ls":
            handle_list_holdings(parts)
        elif command == "/lsstg":
            send_message(handle_list_holdings_by_stage())
        elif command == "/lsstg2":
            send_message(handle_list_holdings_stage("Stage 2"))
        elif command == "/lsstg3":
            send_message(handle_list_holdings_stage("Stage 3"))
        elif command == "/lsstg4":
            send_message(handle_list_holdings_stage("Stage 4"))

        # --- Watchlist ---
        elif command == "/aw":
            send_message(handle_addwatchlist(parts))
        elif command == "/dw":
            send_message(handle_removewatchlist(parts))
        elif command == "/sw":
            send_message(f"🔍 Watchlist scan requested by {username}, running...")
            run_watchlist_scan(notify=True, generate_csv=_wants_csv(parts))
        elif command == "/lw":
            handle_list_watchlist(parts)
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
            send_message(get_help_text())
        elif command == "/helps":
            send_message(get_helps_text())
        elif command == "/helpw":
            send_message(get_helpw_text())

        else:
            send_message(f"Unknown command: `{command}`\n\n{get_help_text()}")
    except Exception as e:
        send_message(f"⚠️ Error handling `{text}`: {e}")
        raise  # re-raise so the Actions run shows as failed in the log


if __name__ == "__main__":
    main()
