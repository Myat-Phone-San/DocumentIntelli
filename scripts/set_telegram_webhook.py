"""Run explicitly after deploying the API. Calls Telegram setWebhook/getWebhookInfo."""
import asyncio
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.settings import ROOT_DIR
from telegram import Bot


async def configure():
    url = os.getenv("PUBLIC_BACKEND_URL", "").strip().rstrip("/")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not url.startswith("https://") or not token or not secret:
        raise ValueError("Set PUBLIC_BACKEND_URL (HTTPS), TELEGRAM_BOT_TOKEN, and TELEGRAM_WEBHOOK_SECRET.")
    async with Bot(token) as bot:
        await bot.set_webhook(url + "/telegram/webhook", secret_token=secret,
                              allowed_updates=["message", "callback_query"], max_connections=1,
                              drop_pending_updates=False)
        info = await bot.get_webhook_info()
        print(f"Webhook configured at {info.url}. Pending updates: {info.pending_update_count}")


if __name__ == "__main__":
    try:
        asyncio.run(configure())
    except Exception as exc:
        print(f"Webhook setup failed ({type(exc).__name__}). Check your settings; token was not displayed.")
        raise SystemExit(1) from None
