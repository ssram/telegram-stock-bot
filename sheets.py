"""
sheets.py
=========
Handles all reads/writes to the Google Sheet that stores stock data.

Sheet columns (in this exact order — row 1 must be a header row with these
names):

    stockName, fullname, sector, industry, quantity, price, cmp,
    stoploss, stage, Type, target, status, marketCap, coreSatellite

- stockName : NSE symbol, e.g. RELIANCE (this is the unique key for lookups)
- fullname  : Company full name (auto-filled from yfinance)
- sector    : Sector (auto-filled from yfinance)
- industry  : Industry (auto-filled from yfinance)
- quantity  : Quantity held (user-provided)
- price     : Buy price (user-provided)
- cmp       : Current market price, live quote (auto-updated on each /ss)
- stoploss  : Stoploss level (user-provided)
- stage     : Weinstein stage, e.g. "Stage 2" (auto-updated on each /ss)
- Type      : e.g. "LongTerm" / "Swing" / "Positional" (user-provided)
- target    : Target price (user-provided, optional — set via /ustgt)
- status    : Entry / Hold / Exit, based on daily + weekly technical
              rules (auto-updated on each /ss). Default: Hold.
              See compute_status() in weinstein_scanner.py for the rules.
- marketCap : Large Cap / Mid Cap / Small Cap / Unknown — auto-filled at
              add time from yfinance's marketCap, using approximate INR
              value bands (NOT SEBI's official rank-based classification,
              since per-company rank isn't available via yfinance):
              Large Cap >= 20,000 Cr, Mid Cap 5,000-20,000 Cr,
              Small Cap < 5,000 Cr. See categorize_market_cap() below.
- coreSatellite : "Core" or "Satellite" (user-provided, optional — set
              via /uscs SYMBOL core|satellite)
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
    "price", "cmp", "stoploss", "stage", "Type", "target", "status",
    "marketCap", "coreSatellite",
]

WATCHLIST_TAB_NAME = "Watchlist"
WATCHLIST_COLUMNS = ["stockName", "fullname", "sector", "industry", "cmp", "stage", "status"]

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
    if not header:
        # Truly empty sheet — safe to create the header row.
        sheet.append_row(COLUMNS)
    elif header != COLUMNS:
        # Header exists but doesn't match — could be a legitimate rename
        # (e.g. investType -> Type) or a manual edit. NEVER clear/wipe the
        # sheet here; that would destroy real data. Just warn so it's
        # visible in the Actions log, and let the mismatch be reconciled
        # manually if it's actually a problem.
        print(
            f"⚠️ WARNING: Sheet header does not match expected columns.\n"
            f"  Expected: {COLUMNS}\n  Found:    {header}\n"
            f"  Proceeding without modifying the sheet."
        )

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
    if not header:
        sheet.append_row(WATCHLIST_COLUMNS)
    elif header != WATCHLIST_COLUMNS:
        print(
            f"⚠️ WARNING: Watchlist header does not match expected columns.\n"
            f"  Expected: {WATCHLIST_COLUMNS}\n  Found:    {header}\n"
            f"  Proceeding without modifying the sheet."
        )

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


def categorize_market_cap(market_cap):
    """
    Classifies a raw market cap (in INR, as returned by yfinance) into
    Large/Mid/Small Cap using approximate value bands — NOT SEBI's
    official rank-based classification (per-company market rank isn't
    available via yfinance, only the raw market cap figure).
      Large Cap: >= 20,000 Cr
      Mid Cap:   5,000-20,000 Cr
      Small Cap: < 5,000 Cr
    """
    if not market_cap:
        return "Unknown"
    crore = market_cap / 1e7  # 1 crore = 1e7
    if crore >= 20000:
        return "Large Cap"
    elif crore >= 5000:
        return "Mid Cap"
    else:
        return "Small Cap"


def get_company_info(symbol):
    """Fetches fullname/sector/industry/marketCap from yfinance for a fresh add."""
    import yfinance as yf
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.get_info()
        return {
            "fullname": info.get("longName", symbol),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "marketCap": categorize_market_cap(info.get("marketCap")),
        }
    except Exception:
        return {
            "fullname": symbol, "sector": "Unknown", "industry": "Unknown",
            "marketCap": "Unknown",
        }


def add_stock(symbol, quantity=0, price=0, stoploss=0, invest_type="Unknown", target=""):
    """Adds a new stock row. Returns a status message string.
    target defaults to blank — set it afterward via /ustgt SYMBOL VALUE.
    marketCap is auto-classified from yfinance at add time.
    coreSatellite defaults to blank — set it afterward via /uscs."""
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
        "",   # cmp — filled on next /ss
        stoploss,
        "",   # stage — filled on next /ss
        invest_type,
        target,
        "Hold",   # status — default; recomputed on next /ss (kept in sync with
                  # weinstein_scanner.py's DEFAULT_STATUS)
        info["marketCap"],   # marketCap — auto-classified, set once at add time
        "",   # coreSatellite — user sets via /uscs
    ]
    sheet.append_row(row)
    return f"✅ Added {symbol} ({info['fullname']}, {info['sector']}, {info['marketCap']})"


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


def batch_update_holdings(updates):
    """
    Writes cmp/stage/status for MANY stocks in a SINGLE Sheets API call,
    instead of one update_cell() call per field per stock. This is what
    avoids Google Sheets' write-quota (60 writes/minute/user) — the old
    per-cell approach could hit that quota with as few as ~15-20 stocks
    in one scan.

    updates: list of dicts, each with keys:
      symbol (required), cmp, stage, status (all optional — only
      fields present are written).
    """
    if not updates:
        return

    sheet = get_sheet()

    # One read call to map every symbol to its row number, instead of a
    # separate lookup per stock.
    col_values = sheet.col_values(1)
    row_map = {
        val.strip().upper(): i
        for i, val in enumerate(col_values, start=1)
        if val.strip()
    }

    field_columns = {
        "cmp": COLUMNS.index("cmp") + 1,
        "stage": COLUMNS.index("stage") + 1,
        "status": COLUMNS.index("status") + 1,
    }

    data = []
    for update in updates:
        symbol = str(update.get("symbol", "")).strip().upper()
        row_num = row_map.get(symbol)
        if not row_num:
            continue  # symbol was removed mid-scan, skip silently

        for field, col_num in field_columns.items():
            if field not in update or update[field] is None:
                continue
            a1 = gspread.utils.rowcol_to_a1(row_num, col_num)
            data.append({"range": a1, "values": [[update[field]]]})

    if data:
        sheet.batch_update(data)  # ONE API call for the whole scan


# ---------------------------------------------------------------------------
# Watchlist — a separate, lighter-weight tab (no quantity/price/stoploss/
# Type, since these aren't holdings, just symbols you're tracking).
# ---------------------------------------------------------------------------

def add_watchlist_stock(symbol):
    """Adds a new symbol to the Watchlist tab."""
    sheet = get_watchlist_sheet()
    symbol = symbol.strip().upper()

    if _find_row(sheet, symbol):
        return f"⚠️ {symbol} is already on the watchlist."

    info = get_company_info(symbol)

    row = [symbol, info["fullname"], info["sector"], info["industry"], "", "", "Hold"]
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


def batch_update_watchlist(updates):
    """
    Writes cmp/stage for MANY watchlist symbols in a SINGLE Sheets API
    call — same quota-avoidance fix as batch_update_holdings above.

    updates: list of dicts, each with keys: symbol (required), cmp,
    stage (both optional — only fields present are written).
    """
    if not updates:
        return

    sheet = get_watchlist_sheet()

    col_values = sheet.col_values(1)
    row_map = {
        val.strip().upper(): i
        for i, val in enumerate(col_values, start=1)
        if val.strip()
    }

    field_columns = {
        "cmp": WATCHLIST_COLUMNS.index("cmp") + 1,
        "stage": WATCHLIST_COLUMNS.index("stage") + 1,
        "status": WATCHLIST_COLUMNS.index("status") + 1,
    }

    data = []
    for update in updates:
        symbol = str(update.get("symbol", "")).strip().upper()
        row_num = row_map.get(symbol)
        if not row_num:
            continue

        for field, col_num in field_columns.items():
            if field not in update or update[field] is None:
                continue
            a1 = gspread.utils.rowcol_to_a1(row_num, col_num)
            data.append({"range": a1, "values": [[update[field]]]})

    if data:
        sheet.batch_update(data)
