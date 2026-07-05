"""
formatting.py
==============
Builds Telegram-friendly tabular output. Telegram has no real table
widget, but wrapping fixed-width, padded text in a code block (triple
backticks) renders as a monospaced, aligned table in every Telegram
client (mobile and desktop).
"""

MAX_TELEGRAM_MESSAGE = 4096
TRUNCATE_NOTICE = "\n... (truncated, list too long for one message)"


def _pad(value, width):
    text = "" if value in (None, "") else str(value)
    if len(text) > width:
        return text[: width - 1] + "…"
    return text.ljust(width)


def build_table(headers, rows, col_widths=None):
    """
    headers: list of column header strings
    rows: list of lists, same column count as headers
    col_widths: optional list of ints; auto-computed from content if omitted

    Returns a Telegram code-block string (wrapped in ``` ```), safe to
    pass straight to send_message().
    """
    if not rows:
        return "_No data to display._"

    if col_widths is None:
        col_widths = []
        for i, header in enumerate(headers):
            longest = max([len(str(header))] + [len(str(r[i])) for r in rows])
            col_widths.append(min(longest, 16))  # cap width so long names don't blow up the table

    header_line = "  ".join(_pad(h, w) for h, w in zip(headers, col_widths))
    separator = "  ".join("-" * w for w in col_widths)

    lines = [header_line, separator]
    for row in rows:
        lines.append("  ".join(_pad(v, w) for v, w in zip(row, col_widths)))

    table_text = "\n".join(lines)
    result = f"```\n{table_text}\n```"

    if len(result) > MAX_TELEGRAM_MESSAGE:
        # Trim rows until it fits, keeping header + separator intact
        while rows and len(result) > MAX_TELEGRAM_MESSAGE - len(TRUNCATE_NOTICE):
            rows = rows[:-1]
            lines = [header_line, separator]
            for row in rows:
                lines.append("  ".join(_pad(v, w) for v, w in zip(row, col_widths)))
            table_text = "\n".join(lines)
            result = f"```\n{table_text}\n```"
        result = result[:-3] + TRUNCATE_NOTICE + "\n```"

    return result


def build_holdings_table(records, title="Holdings"):
    """
    records: list of dicts with keys stockName, quantity, price, cmp,
    stoploss, stage, investType — already sorted by caller.
    """
    if not records:
        return f"*{title}*\n_No stocks found._"

    headers = ["SYMBOL", "QTY", "BUY", "CMP", "SL", "STAGE", "TYPE"]
    rows = []
    for r in records:
        stage = str(r.get("stage", "") or "-").replace("Stage ", "S")
        rows.append([
            r.get("stockName", ""),
            r.get("quantity", ""),
            r.get("price", ""),
            r.get("cmp", "") or "-",
            r.get("stoploss", ""),
            stage,
            r.get("investType", ""),
        ])

    return f"*{title}*\n" + build_table(headers, rows)


def build_watchlist_table(records, title="Watchlist"):
    """
    records: list of dicts with keys stockName, cmp, stage, sector —
    already sorted by caller.
    """
    if not records:
        return f"*{title}*\n_No stocks found._"

    headers = ["SYMBOL", "CMP", "STAGE", "SECTOR"]
    rows = []
    for r in records:
        stage = str(r.get("stage", "") or "-").replace("Stage ", "S")
        rows.append([
            r.get("stockName", ""),
            r.get("cmp", "") or "-",
            stage,
            r.get("sector", "Unknown"),
        ])

    return f"*{title}*\n" + build_table(headers, rows)


def build_grouped_by_stage(records, stage_field="stage", title_prefix="Stage"):
    """
    Groups records by stage (Stage 1/2/3/4) and returns one table per
    stage that actually has stocks, in stage order. Each group's records
    should already be pre-sorted by the caller (by stockName ascending).
    Returns a single combined message string.
    """
    stage_order = ["Stage 1", "Stage 2", "Stage 3", "Stage 4"]
    grouped = {s: [] for s in stage_order}

    for r in records:
        stage = r.get(stage_field)
        if stage in grouped:
            grouped[stage].append(r)

    sections = []
    for stage in stage_order:
        group = grouped[stage]
        if not group:
            continue
        is_watchlist = "sector" in group[0] and "quantity" not in group[0]
        if is_watchlist:
            sections.append(build_watchlist_table(group, title=f"{title_prefix} {stage[-1]} ({len(group)})"))
        else:
            sections.append(build_holdings_table(group, title=f"{title_prefix} {stage[-1]} ({len(group)})"))

    if not sections:
        return "_No stocks with a stage assigned yet. Run a scan first._"

    return "\n\n".join(sections)
