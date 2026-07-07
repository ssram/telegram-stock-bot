"""
weinstein_scanner.py
=====================
Weinstein Stage Analysis scanner.

Reads the list of symbols from the Google Sheet (instead of a local CSV),
analyzes each one on weekly data, writes the latest CMP + Stage back into
the Sheet for every symbol, and posts a summary of current Stage 2 stocks
to Telegram.

Can be run two ways:
  - Directly: `python weinstein_scanner.py` (used by the daily cron workflow)
  - Imported: `from weinstein_scanner import run_scan` (used by /scan command)
"""

import os
import pandas as pd
import yfinance as yf

from sheets import (
    get_all_symbols,
    update_scan_result,
    get_watchlist_symbols,
    update_watchlist_result,
)
from telegram_bot import send_message, send_document
from formatting import build_table

MA_LENGTH = 30
WITHIN_RANGE_PCT = 0
OUTPUT_CSV = "weinstein_stage2.csv"
WATCHLIST_OUTPUT_CSV = "weinstein_watchlist_scan.csv"


def analyze_stock(symbol, ma_length=MA_LENGTH, within_range_pct=WITHIN_RANGE_PCT):
    """
    Runs Weinstein Stage Analysis for a single symbol on weekly data.
    Returns a dict with the latest CMP + Stage always (so callers can write
    it back to the Sheet), plus full detail when the stock is Stage 2.
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

        result = {
            "Sector": sector,
            "Company": company,
            "Symbol": symbol,
            "CMP": cmp,
            "Stage": stage,
            "is_stage2": stage == "Stage 2",
        }

        if stage == "Stage 2":
            sma30 = round(latest["SMA30"], 2)
            pct_above_sma = round(((cmp - sma30) / sma30) * 100, 2)
            sma_rising = latest["SMA30"] > df.iloc[-2]["SMA30"]
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
    Runs the full scan over every symbol in the Google Sheet.
    Writes CMP + Stage back to the Sheet for every symbol (Stage 2 or not),
    unconditionally — this happens regardless of whether the symbol is
    Stage 2 or not, and regardless of generate_csv/notify.
    Sends a Telegram summary table if notify=True.
    Only generates and attaches the CSV file if generate_csv=True — this
    avoids writing/uploading a file on every single scan when nobody asked
    for it.
    """
    symbols = get_all_symbols()

    if not symbols:
        if notify:
            send_message("⚠️ No stocks in the sheet yet. Use /as to add some.")
        return

    stage2_results = []

    for symbol in symbols:
        print(f"Scanning {symbol}")
        result = analyze_stock(symbol)

        if result is None:
            continue

        # Always write the latest CMP + Stage back to the Sheet, for every
        # symbol scanned — not just Stage 2 hits.
        update_scan_result(symbol, result["CMP"], result["Stage"])

        if result["is_stage2"]:
            result.pop("is_stage2")
            stage2_results.append(result)

    if not stage2_results:
        if notify:
            send_message("❌ No Stage 2 stocks found in this scan.")
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
            f"📈 *Stage 2 scan complete* — {len(stage2_results)} stock(s) found\n"
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
    Writes CMP + Stage back for EVERY symbol scanned — unconditionally,
    regardless of whether it's Stage 2 or not — this is what keeps the
    Watchlist tab's cmp/stage columns current on every /sw run.
    Only generates/attaches the CSV if generate_csv=True.
    """
    symbols = get_watchlist_symbols()

    if not symbols:
        if notify:
            send_message("⚠️ Watchlist is empty. Use /aw SYMBOL to add some.")
        return

    stage2_results = []

    for symbol in symbols:
        print(f"Scanning watchlist symbol {symbol}")
        result = analyze_stock(symbol)

        if result is None:
            # analyze_stock returned None (e.g. insufficient history, or a
            # yfinance fetch error) — cmp/stage cannot be updated for this
            # symbol this run, since no data was returned at all.
            print(f"  -> no result for {symbol}, cmp/stage not updated this run")
            continue

        # This runs for every symbol scanned, Stage 2 or not — confirmed
        # unconditional, not inside the is_stage2 branch below.
        update_watchlist_result(symbol, result["CMP"], result["Stage"])

        if result["is_stage2"]:
            result.pop("is_stage2")
            stage2_results.append(result)

    if not stage2_results:
        if notify:
            send_message("👀 Watchlist scan complete — no Stage 2 stocks right now.")
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
            f"👀 *Watchlist scan complete* — {len(stage2_results)} Stage 2 stock(s) found\n"
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
