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

_sheet_cache = None


def get_sheet():
    """Returns the gspread worksheet object (cached per-process)."""
    global _sheet_cache
    if _sheet_cache is not None:
        return _sheet_cache

    creds_dict = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)

    sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "Stocks Master")
    sheet = client.open(sheet_name).sheet1

    # Ensure header row exists and matches expected columns
    header = sheet.row_values(1)
    if header != COLUMNS:
        sheet.clear()
        sheet.append_row(COLUMNS)

    _sheet_cache = sheet
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
        return f"⚠️ {symbol} already exists. Use /updatestock to change it."

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
        return f"⚠️ {symbol.upper()} not found. Use /addstock first."

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


def list_stocks():
    sheet = get_sheet()
    records = sheet.get_all_records()

    if not records:
        return "Sheet is empty. Use /addstock SYMBOL QTY PRICE STOPLOSS INVESTTYPE to add one."

    lines = []
    for r in records:
        lines.append(
            f"{r['stockName']} | Qty:{r['quantity']} Buy:{r['price']} "
            f"CMP:{r.get('cmp', '-')} SL:{r['stoploss']} "
            f"Stage:{r.get('stage', '-')} ({r['investType']})"
        )
    return "📋 Current holdings:\n" + "\n".join(lines)


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
