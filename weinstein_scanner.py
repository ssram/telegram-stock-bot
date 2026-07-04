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
        cmp = round(latest["Close"], 2)
        stage = latest["Stage"]

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


def run_scan(notify=True):
    """
    Runs the full scan over every symbol in the Google Sheet.
    Writes CMP + Stage back to the Sheet for every symbol (Stage 2 or not).
    Sends a Telegram summary + CSV of Stage 2 stocks if notify=True.
    """
    symbols = get_all_symbols()

    if not symbols:
        if notify:
            send_message("⚠️ No stocks in the sheet yet. Use /addstock to add some.")
        return

    stage2_results = []

    for symbol in symbols:
        print(f"Scanning {symbol}")
        result = analyze_stock(symbol)

        if result is None:
            continue

        # Always write the latest CMP + Stage back to the Sheet
        update_scan_result(symbol, result["CMP"], result["Stage"])

        if result["is_stage2"]:
            result.pop("is_stage2")
            stage2_results.append(result)

    if not stage2_results:
        if notify:
            send_message("❌ No Stage 2 stocks found in this scan.")
        return

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
        summary_lines = [
            f"{r['Symbol']} | {r['% Above SMA']}% above 30W SMA | "
            f"{r['Weeks in Stage2']}w in Stage2"
            for r in stage2_results[:15]
        ]
        summary = (
            f"📈 *Stage 2 scan complete* — {len(stage2_results)} stock(s) found\n\n"
            + "\n".join(summary_lines)
        )
        if len(stage2_results) > 15:
            summary += f"\n...and {len(stage2_results) - 15} more (see attached CSV)"

        send_message(summary)
        send_document(OUTPUT_CSV, caption="Full Stage 2 scan results")


def run_watchlist_scan(notify=True):
    """
    Runs Weinstein Stage Analysis over every symbol on the Watchlist tab.
    Writes CMP + Stage back for every symbol, and sends a Telegram summary
    of which watchlist stocks are currently Stage 2 (potential entries).
    """
    symbols = get_watchlist_symbols()

    if not symbols:
        if notify:
            send_message("⚠️ Watchlist is empty. Use /addwatchlist SYMBOL to add some.")
        return

    stage2_results = []

    for symbol in symbols:
        print(f"Scanning watchlist symbol {symbol}")
        result = analyze_stock(symbol)

        if result is None:
            continue

        update_watchlist_result(symbol, result["CMP"], result["Stage"])

        if result["is_stage2"]:
            result.pop("is_stage2")
            stage2_results.append(result)

    if not stage2_results:
        if notify:
            send_message("👀 Watchlist scan complete — no Stage 2 stocks right now.")
        return

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
        summary_lines = [
            f"{r['Symbol']} | {r['% Above SMA']}% above 30W SMA | "
            f"{r['Weeks in Stage2']}w in Stage2"
            for r in stage2_results[:15]
        ]
        summary = (
            f"👀 *Watchlist scan complete* — {len(stage2_results)} Stage 2 stock(s) found\n\n"
            + "\n".join(summary_lines)
        )
        if len(stage2_results) > 15:
            summary += f"\n...and {len(stage2_results) - 15} more (see attached CSV)"

        send_message(summary)
        send_document(WATCHLIST_OUTPUT_CSV, caption="Full watchlist scan results")


if __name__ == "__main__":
    run_scan(notify=True)
