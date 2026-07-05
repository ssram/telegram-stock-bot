"""
sheets.py
=========
Handles all reads/writes to the Google Sheet that stores stock data.

Sheet columns (in this exact order — row 1 must be a header row with these
names):

    stockName, fullname, sector, industry, quantity, price, cmp,
    stoploss, stage, investType

- stockName : NSE symbol, e.g. RELIANCE (this is the unique key for lookups)
- fullname  : Company full name (auto-filled from yfinance)
- sector    : Sector (auto-filled from yfinance)
- industry  : Industry (auto-filled from yfinance)
- quantity  : Quantity held (user-provided)
- price     : Buy price (user-provided)
- cmp       : Current market price (auto-updated on each /scan)
- stoploss  : Stoploss level (user-provided)
- stage     : Weinstein stage, e.g. "Stage 2" (auto-updated on each /scan)
- investType: e.g. "LongTerm" / "Swing" / "Positional" (user-provided)
"""

import os
import json
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

COLUMNS = [
    "stockName", "fullname", "sector", "industry", "quantity",
    "price", "cmp", "stoploss", "stage", "investType",
]

WATCHLIST_TAB_NAME = "Watchlist"
WATCHLIST_COLUMNS = ["stockName", "fullname", "sector", "industry", "cmp", "stage"]

_sheet_cache = None
_spreadsheet_cache = None
_watchlist_sheet_cache = None


def _get_spreadsheet():
    """Returns the gspread Spreadsheet object (cached per-process)."""
    global _spreadsheet_cache
    if _spreadsheet_cache is not None:
        return _spreadsheet_cache

    creds_dict = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)

    sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "Stocks Master")
    _spreadsheet_cache = client.open(sheet_name)
    return _spreadsheet_cache


def get_sheet():
    """Returns the main holdings worksheet (first tab), cached per-process."""
    global _sheet_cache
    if _sheet_cache is not None:
        return _sheet_cache

    spreadsheet = _get_spreadsheet()
    sheet = spreadsheet.sheet1

    header = sheet.row_values(1)
    if header != COLUMNS:
        sheet.clear()
        sheet.append_row(COLUMNS)

    _sheet_cache = sheet
    return sheet


def get_watchlist_sheet():
    """Returns the Watchlist tab, creating it if it doesn't exist yet."""
    global _watchlist_sheet_cache
    if _watchlist_sheet_cache is not None:
        return _watchlist_sheet_cache

    spreadsheet = _get_spreadsheet()

    try:
        sheet = spreadsheet.worksheet(WATCHLIST_TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(
            title=WATCHLIST_TAB_NAME, rows=200, cols=len(WATCHLIST_COLUMNS)
        )
        sheet.append_row(WATCHLIST_COLUMNS)

    header = sheet.row_values(1)
    if header != WATCHLIST_COLUMNS:
        sheet.clear()
        sheet.append_row(WATCHLIST_COLUMNS)

    _watchlist_sheet_cache = sheet
    return sheet


def _find_row(sheet, symbol):
    """Returns the 1-indexed row number for a symbol, or None if not found."""
    symbol = symbol.strip().upper()
    col_values = sheet.col_values(1)  # stockName column
    for i, val in enumerate(col_values, start=1):
        if val.strip().upper() == symbol:
            return i
    return None


def get_company_info(symbol):
    """Fetches fullname/sector/industry from yfinance for a fresh add."""
    import yfinance as yf
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.get_info()
        return {
            "fullname": info.get("longName", symbol),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
        }
    except Exception:
        return {"fullname": symbol, "sector": "Unknown", "industry": "Unknown"}


def add_stock(symbol, quantity=0, price=0, stoploss=0, invest_type="Unknown"):
    """Adds a new stock row. Returns a status message string."""
    sheet = get_sheet()
    symbol = symbol.strip().upper()

    if _find_row(sheet, symbol):
        return f"⚠️ {symbol} already exists. Use /us to change it."

    info = get_company_info(symbol)

    row = [
        symbol,
        info["fullname"],
        info["sector"],
        info["industry"],
        quantity,
        price,
        "",   # cmp — filled on next /scan
        stoploss,
        "",   # stage — filled on next /scan
        invest_type,
    ]
    sheet.append_row(row)
    return f"✅ Added {symbol} ({info['fullname']}, {info['sector']})"


def update_stock(symbol, **fields):
    """
    Updates one or more fields for an existing stock.
    fields keys must match COLUMNS (e.g. quantity=20, stoploss=1200).
    """
    sheet = get_sheet()
    row_num = _find_row(sheet, symbol)

    if not row_num:
        return f"⚠️ {symbol.upper()} not found. Use /as first."

    updated = []
    for key, value in fields.items():
        if key not in COLUMNS:
            continue
        col_num = COLUMNS.index(key) + 1
        sheet.update_cell(row_num, col_num, value)
        updated.append(f"{key}={value}")

    if not updated:
        return "⚠️ No valid fields to update."

    return f"✅ Updated {symbol.upper()}: " + ", ".join(updated)


def remove_stock(symbol):
    sheet = get_sheet()
    row_num = _find_row(sheet, symbol)

    if not row_num:
        return f"⚠️ {symbol.upper()} not found."

    sheet.delete_rows(row_num)
    return f"🗑️ Removed {symbol.upper()}."


def get_all_holdings_records():
    """Returns all holdings records, sorted ascending by stockName."""
    sheet = get_sheet()
    records = sheet.get_all_records()
    records = [r for r in records if r.get("stockName")]
    records.sort(key=lambda r: str(r.get("stockName", "")).upper())
    return records


def get_holdings_by_stage(stage):
    """Returns holdings records matching the given stage exactly (e.g. 'Stage 2'),
    sorted ascending by stockName."""
    return [r for r in get_all_holdings_records() if r.get("stage") == stage]


def get_all_symbols():
    """Used by the scanner to know which symbols to analyze."""
    sheet = get_sheet()
    records = sheet.get_all_records()
    return [r["stockName"] for r in records if r.get("stockName")]


def update_scan_result(symbol, cmp_value, stage):
    """Called by the scanner after analyzing a symbol, to write back cmp + stage."""
    sheet = get_sheet()
    row_num = _find_row(sheet, symbol)

    if not row_num:
        return  # symbol was removed mid-scan, skip silently

    cmp_col = COLUMNS.index("cmp") + 1
    stage_col = COLUMNS.index("stage") + 1

    sheet.update_cell(row_num, cmp_col, cmp_value)
    sheet.update_cell(row_num, stage_col, stage)


# ---------------------------------------------------------------------------
# Watchlist — a separate, lighter-weight tab (no quantity/price/stoploss/
# investType, since these aren't holdings, just symbols you're tracking).
# ---------------------------------------------------------------------------

def add_watchlist_stock(symbol):
    """Adds a new symbol to the Watchlist tab."""
    sheet = get_watchlist_sheet()
    symbol = symbol.strip().upper()

    if _find_row(sheet, symbol):
        return f"⚠️ {symbol} is already on the watchlist."

    info = get_company_info(symbol)

    row = [symbol, info["fullname"], info["sector"], info["industry"], "", ""]
    sheet.append_row(row)
    return f"✅ Added {symbol} ({info['fullname']}) to watchlist."


def remove_watchlist_stock(symbol):
    sheet = get_watchlist_sheet()
    row_num = _find_row(sheet, symbol)

    if not row_num:
        return f"⚠️ {symbol.upper()} not found on the watchlist."

    sheet.delete_rows(row_num)
    return f"🗑️ Removed {symbol.upper()} from watchlist."


def get_all_watchlist_records():
    """Returns all watchlist records, sorted ascending by stockName."""
    sheet = get_watchlist_sheet()
    records = sheet.get_all_records()
    records = [r for r in records if r.get("stockName")]
    records.sort(key=lambda r: str(r.get("stockName", "")).upper())
    return records


def get_watchlist_by_stage(stage):
    """Returns watchlist records matching the given stage exactly (e.g. 'Stage 1'),
    sorted ascending by stockName."""
    return [r for r in get_all_watchlist_records() if r.get("stage") == stage]


def get_watchlist_symbols():
    """Used by the watchlist scanner to know which symbols to analyze."""
    sheet = get_watchlist_sheet()
    records = sheet.get_all_records()
    return [r["stockName"] for r in records if r.get("stockName")]


def update_watchlist_result(symbol, cmp_value, stage):
    """Called by the watchlist scanner after analyzing a symbol."""
    sheet = get_watchlist_sheet()
    row_num = _find_row(sheet, symbol)

    if not row_num:
        return

    cmp_col = WATCHLIST_COLUMNS.index("cmp") + 1
    stage_col = WATCHLIST_COLUMNS.index("stage") + 1

    sheet.update_cell(row_num, cmp_col, cmp_value)
    sheet.update_cell(row_num, stage_col, stage)
