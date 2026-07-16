"""
weinstein_scanner.py
=====================
Weinstein Stage Analysis scanner.

Reads holdings from the Google Sheet, analyzes each one on weekly data,
and writes results back in a SINGLE batched API call per scan (not one
call per cell) — this avoids hitting Google Sheets' write-quota (60
writes/minute/user), which individual update_cell() calls blew past once
enough stocks were being scanned.

Can be run two ways:
  - Directly: `python weinstein_scanner.py` (used by the daily cron workflow)
  - Imported: `from weinstein_scanner import run_scan` (used by /ss command)
"""

import os
import pandas as pd
import yfinance as yf

from sheets import (
    get_all_holdings_records,
    batch_update_holdings,
    get_watchlist_symbols,
    batch_update_watchlist,
)
from telegram_bot import send_message, send_document
from formatting import build_table

MA_LENGTH = 30
WITHIN_RANGE_PCT = 0
OUTPUT_CSV = "weinstein_stage2.csv"
WATCHLIST_OUTPUT_CSV = "weinstein_watchlist_scan.csv"

DEFAULT_STATUS = "Hold"


def compute_status(weekly_sma_rising, weekly_below_sma30, daily):
    """
    Determines Entry / Hold / Exit based on weekly + daily signals.
    Priority: Exit conditions checked first (any true -> Exit), then
    Entry (all true -> Entry), otherwise Hold (the default).

    daily is a dict with keys: close, prev_close, ema10, prev_ema10,
    ema21, ema50 — or None if daily data couldn't be fetched, in which
    case this safely falls back to Hold (can't evaluate day-based rules
    without daily data).

    Exit (ANY true):
      1. Close below EMA10 for 2 consecutive days
      2. Close below EMA21
      3. EMA10 < EMA21
      4. Close below EMA50
      5. Weekly close below the 30-week MA

    Entry (ALL true, and no exit condition triggered):
      1. Weekly 30W MA is rising
      2. Daily close > EMA10
      3. EMA10 > EMA21
      4. EMA21 > EMA50
    """
    if daily is None:
        return DEFAULT_STATUS

    exit_signal = (
        (daily["close"] < daily["ema10"] and daily["prev_close"] < daily["prev_ema10"])
        or (daily["close"] < daily["ema21"])
        or (daily["ema10"] < daily["ema21"])
        or (daily["close"] < daily["ema50"])
        or weekly_below_sma30
    )
    if exit_signal:
        return "Exit"

    entry_signal = (
        weekly_sma_rising
        and daily["close"] > daily["ema10"]
        and daily["ema10"] > daily["ema21"]
        and daily["ema21"] > daily["ema50"]
    )
    if entry_signal:
        return "Entry"

    return DEFAULT_STATUS


def get_daily_signals(ticker):
    """
    Fetches daily candles and returns the latest + previous day's close
    and EMA10, plus latest EMA21/EMA50 — everything compute_status()
    needs. Returns None if there's not enough daily history to compute
    a stable EMA50 (needs a meaningful warm-up period, not just 50 bars).
    """
    try:
        df = ticker.history(period="6mo", interval="1d", auto_adjust=True)
        if df is None or len(df) < 55:
            return None

        df["EMA10"] = df["Close"].ewm(span=10, adjust=False).mean()
        df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        return {
            "close": latest["Close"],
            "prev_close": prev["Close"],
            "ema10": latest["EMA10"],
            "prev_ema10": prev["EMA10"],
            "ema21": latest["EMA21"],
            "ema50": latest["EMA50"],
        }
    except Exception as e:
        print(f"  -> daily signal fetch failed: {e}")
        return None


def analyze_stock(symbol, ma_length=MA_LENGTH, within_range_pct=WITHIN_RANGE_PCT):
    """
    Runs Weinstein Stage Analysis for a single symbol on weekly data.
    Returns a dict with the latest CMP + Stage + Entry/Hold/Exit status
    always (so callers can write it back to the Sheet), plus full detail
    when the stock is Stage 2.
    """
    try:
        yf_symbol = f"{symbol}.NS"
        ticker = yf.Ticker(yf_symbol)

        try:
            info = ticker.get_info()
            company = info.get("longName", symbol)
            sector = info.get("sector", "Unknown")
        except Exception:
            company = symbol
            sector = "Unknown"

        df = ticker.history(period="3y", interval="1wk", auto_adjust=True)

        if len(df) < ma_length:
            return None

        df["SMA30"] = df["Close"].rolling(ma_length).mean()
        df = df.dropna()

        trend = "down"
        stages = []
        stage2_weeks = []
        consecutive_stage2 = 0

        for _, row in df.iterrows():
            sma = row["SMA30"]
            body_low = min(row["Open"], row["Close"])
            body_high = max(row["Open"], row["Close"])
            within_range = sma * (within_range_pct / 100)

            if body_low > sma + within_range:
                trend = "up"
                stage = "Stage 2"
            elif body_high < sma - within_range:
                trend = "down"
                stage = "Stage 4"
            else:
                stage = "Stage 3" if trend == "up" else "Stage 1"

            stages.append(stage)

            if stage == "Stage 2":
                consecutive_stage2 += 1
            else:
                consecutive_stage2 = 0
            stage2_weeks.append(consecutive_stage2)

        df["Stage"] = stages
        df["Stage2Weeks"] = stage2_weeks

        latest = df.iloc[-1]
        weekly_close = round(latest["Close"], 2)
        stage = latest["Stage"]

        # These two weekly checks feed compute_status() below, and are
        # needed for every stock regardless of stage — not just Stage 2.
        weekly_sma_rising = latest["SMA30"] > df.iloc[-2]["SMA30"]
        weekly_below_sma30 = latest["Close"] < latest["SMA30"]

        # cmp should reflect the actual current/live market price, not the
        # latest weekly candle's close — those can differ meaningfully
        # (the current week's candle isn't finalized yet, or the stock
        # moved intraday since the last weekly bar was formed). Fetch a
        # live quote separately; fall back to the weekly close only if
        # that fails for some reason.
        cmp = weekly_close
        try:
            try:
                live_price = ticker.fast_info["last_price"]
            except Exception:
                live_price = ticker.fast_info.last_price
            if live_price:
                cmp = round(live_price, 2)
        except Exception:
            pass  # keep weekly_close as the fallback

        # Daily data drives the Entry/Hold/Exit status — separate fetch
        # from the weekly data above, since they're different timeframes
        # answering different questions (weekly = long-term stage,
        # daily = short-term entry/exit timing).
        daily_signals = get_daily_signals(ticker)
        status = compute_status(weekly_sma_rising, weekly_below_sma30, daily_signals)

        result = {
            "Sector": sector,
            "Company": company,
            "Symbol": symbol,
            "CMP": cmp,
            "Stage": stage,
            "Status": status,
            "is_stage2": stage == "Stage 2",
        }

        if stage == "Stage 2":
            sma30 = round(latest["SMA30"], 2)
            pct_above_sma = round(((cmp - sma30) / sma30) * 100, 2)
            sma_rising = weekly_sma_rising
            high52 = df["High"].tail(52).max()
            pct_from_high = round(((cmp - high52) / high52) * 100, 2)
            avg_volume = df["Volume"].tail(10).mean()
            volume_ratio = round(latest["Volume"] / avg_volume, 2) if avg_volume else 0

            result.update({
                "Weeks in Stage2": int(latest["Stage2Weeks"]),
                "30W SMA": sma30,
                "% Above SMA": pct_above_sma,
                "30W SMA Rising": sma_rising,
                "52W High": round(high52, 2),
                "% From 52W High": pct_from_high,
                "Volume Ratio": volume_ratio,
            })

        return result

    except Exception as e:
        print(f"{symbol} : {e}")
        return None


def run_scan(notify=True, generate_csv=False):
    """
    Runs the full scan over every holding in the Google Sheet.
    Writes cmp/stage/status back for every symbol scanned (Stage 2 or
    not), regardless of generate_csv/notify — but all in ONE batched
    Sheets API call at the end, not one call per cell per stock.
    Sends a Telegram summary table if notify=True.
    Only generates and attaches the CSV if generate_csv=True.
    """
    holdings = get_all_holdings_records()

    if not holdings:
        if notify:
            send_message("⚠️ No stocks in the sheet yet. Use /as to add some.")
        return

    stage2_results = []
    sheet_updates = []

    for holding in holdings:
        symbol = holding.get("stockName")
        if not symbol:
            continue

        print(f"Scanning {symbol}")
        result = analyze_stock(symbol)

        if result is None:
            continue

        sheet_updates.append({
            "symbol": symbol,
            "cmp": result["CMP"],
            "stage": result["Stage"],
            "status": result["Status"],
        })

        if result["is_stage2"]:
            result.pop("is_stage2")
            stage2_results.append(result)

    # Single batched write for the entire scan, regardless of how many
    # stocks were scanned — this is what avoids the write-quota error.
    batch_update_holdings(sheet_updates)

    scan_header = f"✅ Scan complete — {len(sheet_updates)} stock(s) scanned."

    if not stage2_results:
        if notify:
            send_message(f"{scan_header}\n📊 No stocks currently in Stage 2.")
        return

    if generate_csv:
        output = pd.DataFrame(stage2_results)
        output.sort_values(
            by=["Sector", "Weeks in Stage2", "% Above SMA"],
            ascending=[True, False, False],
            inplace=True,
        )
        output.to_csv(OUTPUT_CSV, index=False)
        print(output)
        print(f"\n✅ Saved: {OUTPUT_CSV}")

    if notify:
        # Ascending by symbol for the on-screen table.
        table_rows = sorted(stage2_results, key=lambda r: r["Symbol"])
        headers = ["SYMBOL", "% ABOVE SMA", "WEEKS"]
        rows = [
            [r["Symbol"], r["% Above SMA"], r["Weeks in Stage2"]]
            for r in table_rows[:30]
        ]
        summary = (
            f"{scan_header}\n📈 {len(stage2_results)} in Stage 2:\n"
            + build_table(headers, rows)
        )
        if len(stage2_results) > 30:
            summary += f"\n_...and {len(stage2_results) - 30} more_"
        if not generate_csv:
            summary += "\n_Send `/ss csv` to get a downloadable file._"

        send_message(summary)
        if generate_csv:
            send_document(OUTPUT_CSV, caption="Full Stage 2 scan results")


def run_watchlist_scan(notify=True, generate_csv=False):
    """
    Runs Weinstein Stage Analysis over every symbol on the Watchlist tab.
    Writes cmp/stage back for EVERY symbol scanned in ONE batched Sheets
    API call at the end (same quota-avoidance fix as run_scan above).
    Only generates/attaches the CSV if generate_csv=True.
    """
    symbols = get_watchlist_symbols()

    if not symbols:
        if notify:
            send_message("⚠️ Watchlist is empty. Use /aw SYMBOL to add some.")
        return

    stage2_results = []
    sheet_updates = []

    for symbol in symbols:
        print(f"Scanning watchlist symbol {symbol}")
        result = analyze_stock(symbol)

        if result is None:
            print(f"  -> no result for {symbol}, cmp/stage not updated this run")
            continue

        # Status is computed identically here as for holdings — it never
        # depended on Type in the first place, just weekly + daily signals.
        sheet_updates.append({
            "symbol": symbol,
            "cmp": result["CMP"],
            "stage": result["Stage"],
            "status": result["Status"],
        })

        if result["is_stage2"]:
            result.pop("is_stage2")
            stage2_results.append(result)

    batch_update_watchlist(sheet_updates)

    scan_header = f"✅ Watchlist scan complete — {len(sheet_updates)} stock(s) scanned."

    if not stage2_results:
        if notify:
            send_message(f"{scan_header}\n📊 No stocks currently in Stage 2.")
        return

    if generate_csv:
        output = pd.DataFrame(stage2_results)
        output.sort_values(
            by=["Sector", "Weeks in Stage2", "% Above SMA"],
            ascending=[True, False, False],
            inplace=True,
        )
        output.to_csv(WATCHLIST_OUTPUT_CSV, index=False)
        print(output)
        print(f"\n✅ Saved: {WATCHLIST_OUTPUT_CSV}")

    if notify:
        table_rows = sorted(stage2_results, key=lambda r: r["Symbol"])
        headers = ["SYMBOL", "% ABOVE SMA", "WEEKS"]
        rows = [
            [r["Symbol"], r["% Above SMA"], r["Weeks in Stage2"]]
            for r in table_rows[:30]
        ]
        summary = (
            f"{scan_header}\n📈 {len(stage2_results)} in Stage 2:\n"
            + build_table(headers, rows)
        )
        if len(stage2_results) > 30:
            summary += f"\n_...and {len(stage2_results) - 30} more_"
        if not generate_csv:
            summary += "\n_Send `/sw csv` to get a downloadable file._"

        send_message(summary)
        if generate_csv:
            send_document(WATCHLIST_OUTPUT_CSV, caption="Full watchlist scan results")


if __name__ == "__main__":
    run_scan(notify=True)
