"""
nifty_signal.py
=================
Mechanical, rule-based Nifty trend signal — NOT financial advice. This is
a technical screener output, same spirit as the Weinstein stage scanner,
just on a much shorter timeframe suited to options.

Rule (as specified):
  - 5-minute candles on the Nifty 50 index (^NSEI)
  - EMA9 vs EMA21 crossover
  - Price-action confirmation: the latest candle's close must also be
    above both EMAs (bullish) or below both EMAs (bearish) — a bare
    EMA crossover without this confirmation does not count as a signal
  - ATM strike = nearest 50-point strike to current spot
  - SL/target are expressed in NIFTY INDEX POINTS, not option premium —
    there is no live option-chain/premium data source wired in yet
    (planned alongside the future Dhan API integration). Translate to
    actual premium manually via your broker until that's added.
"""

import yfinance as yf

STRIKE_INTERVAL = 50
SL_POINTS = 30
TARGET_POINTS = 60


def get_nifty_signal():
    """
    Returns a dict describing the current signal, or None if the fetch
    failed entirely (e.g. no data available).
    """
    try:
        ticker = yf.Ticker("^NSEI")
        df = ticker.history(period="5d", interval="5m")

        if df is None or len(df) < 21:
            return None

        df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
        df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()

        latest = df.iloc[-1]
        close = latest["Close"]
        ema9 = latest["EMA9"]
        ema21 = latest["EMA21"]
        candle_time = df.index[-1]

        bullish = (ema9 > ema21) and (close > ema9) and (close > ema21)
        bearish = (ema9 < ema21) and (close < ema9) and (close < ema21)

        atm_strike = round(close / STRIKE_INTERVAL) * STRIKE_INTERVAL

        result = {
            "spot": round(close, 2),
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "candle_time": candle_time,
            "atm_strike": atm_strike,
        }

        if bullish:
            result.update({
                "trend": "bullish",
                "direction": "CE",
                "nifty_sl": round(close - SL_POINTS, 2),
                "nifty_target": round(close + TARGET_POINTS, 2),
            })
        elif bearish:
            result.update({
                "trend": "bearish",
                "direction": "PE",
                "nifty_sl": round(close + SL_POINTS, 2),
                "nifty_target": round(close - TARGET_POINTS, 2),
            })
        else:
            result.update({"trend": "neutral", "direction": None})

        return result

    except Exception as e:
        print(f"Nifty signal fetch failed: {e}")
        return None


def format_nifty_signal_message(signal):
    if signal is None:
        return "⚠️ Couldn't fetch Nifty data right now. Try again in a bit."

    time_str = signal["candle_time"].strftime("%Y-%m-%d %H:%M")
    lines = [
        "*Nifty Trend Signal*",
        f"_as of {time_str} (last 5-min candle)_",
        "",
        f"Spot: `{signal['spot']}`  |  EMA9: `{signal['ema9']}`  |  EMA21: `{signal['ema21']}`",
        "",
    ]

    if signal["trend"] == "bullish":
        lines.append("📈 *Bullish* — EMA9 > EMA21, price above both EMAs")
        lines.append("")
        lines.append(f"Suggested: *NIFTY {signal['atm_strike']} {signal['direction']}* (ATM)")
        lines.append(f"Nifty SL: `{signal['nifty_sl']}` (-{SL_POINTS} pts)")
        lines.append(f"Nifty Target: `{signal['nifty_target']}` (+{TARGET_POINTS} pts)")
    elif signal["trend"] == "bearish":
        lines.append("📉 *Bearish* — EMA9 < EMA21, price below both EMAs")
        lines.append("")
        lines.append(f"Suggested: *NIFTY {signal['atm_strike']} {signal['direction']}* (ATM)")
        lines.append(f"Nifty SL: `{signal['nifty_sl']}` (+{SL_POINTS} pts)")
        lines.append(f"Nifty Target: `{signal['nifty_target']}` (-{TARGET_POINTS} pts)")
    else:
        lines.append("➖ *Neutral* — no confirmed EMA9/EMA21 crossover right now")
        lines.append("_No trade suggested._")

    lines.append("")
    lines.append(
        "_SL/Target shown in Nifty index points, not option premium — "
        "translate manually via your broker's option chain until live "
        "premium data is integrated. This is a mechanical technical "
        "signal, not financial advice. Options trading carries high risk._"
    )

    return "\n".join(lines)
