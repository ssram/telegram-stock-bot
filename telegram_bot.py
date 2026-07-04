"""
telegram_bot.py
================
Thin wrapper around the Telegram Bot API for sending messages/files and
polling for new commands.
"""

import os
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(text):
    """Sends a plain text message to the shared team chat."""
    try:
        resp = requests.post(
            f"{API_URL}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[telegram_bot] Failed to send message: {e}")


def send_document(file_path, caption=""):
    """Sends a file (e.g. the scan results CSV) to the shared team chat."""
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{API_URL}/sendDocument",
                data={"chat_id": CHAT_ID, "caption": caption},
                files={"document": f},
                timeout=60,
            )
        resp.raise_for_status()
    except Exception as e:
        print(f"[telegram_bot] Failed to send document: {e}")


def get_updates(offset=None):
    """Fetches new messages since the given update_id offset."""
    params = {"timeout": 10}
    if offset:
        params["offset"] = offset

    try:
        resp = requests.get(f"{API_URL}/getUpdates", params=params, timeout=20)
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception as e:
        print(f"[telegram_bot] Failed to get updates: {e}")
        return []
