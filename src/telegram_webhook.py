"""Atlas-backed webhook inbox so accepted updates survive a backend restart."""
import asyncio
from contextlib import suppress
from datetime import timedelta
import logging
import os
import secrets

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from src.mongo_store import get_database, now

logger = logging.getLogger(__name__)


def claim(database):
    from pymongo import ReturnDocument
    timestamp = now()
    return database.telegram_inbox.find_one_and_update({"$or": [
        {"state": "pending"}, {"state": "processing", "lease_until": {"$lt": timestamp}}]},
        {"$set": {"state": "processing", "lease_until": timestamp + timedelta(minutes=10)}},
        return_document=ReturnDocument.AFTER)


async def process_inbox(application, database):
    from telegram import Update
    import httpx
    from telegram_bot import API_URL
    # Lifespan starts before uvicorn binds its socket. Don't consume backlog until
    # the internal API is listening, or a restart could fail pending questions.
    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            try:
                response = await client.get(API_URL + "/health")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    while True:
        try:
            item = await run_in_threadpool(claim, database)
            if not item:
                await asyncio.sleep(1)
                continue
            await application.process_update(Update.de_json(item["payload"], application.bot))
            await run_in_threadpool(database.telegram_inbox.update_one, {"_id": item["_id"]},
                {"$set": {"state": "done", "expires_at": now() + timedelta(days=7)}})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Webhook processing failed: %s", type(exc).__name__)
            await asyncio.sleep(5)


async def start(app):
    if not os.getenv("MONGODB_URI", "").strip() or not os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip():
        raise RuntimeError("Webhook mode requires MONGODB_URI and TELEGRAM_WEBHOOK_SECRET.")
    from telegram_bot import build_application
    application = build_application()
    database = await run_in_threadpool(get_database)
    await application.initialize()
    await application.start()
    app.state.telegram_application = application
    app.state.telegram_database = database
    app.state.telegram_worker = asyncio.create_task(process_inbox(application, database))


async def stop(app):
    app.state.telegram_worker.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.telegram_worker
    await app.state.telegram_application.stop()
    await app.state.telegram_application.shutdown()


async def receive(request: Request):
    expected = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not expected or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(403, "Invalid webhook credentials.")
    if not hasattr(request.app.state, "telegram_application"):
        raise HTTPException(503, "Webhook mode is not enabled.")
    if len(await request.body()) > 1024 * 1024:
        raise HTTPException(413, "Update too large.")
    try:
        payload = await request.json()
        if not isinstance(payload, dict) or type(payload.get("update_id")) is not int:
            raise ValueError("Invalid update")
        await run_in_threadpool(request.app.state.telegram_database.telegram_inbox.update_one,
            {"_id": payload["update_id"]}, {"$setOnInsert": {"payload": payload, "state": "pending", "received_at": now()}}, upsert=True)
    except ValueError:
        raise HTTPException(400, "Invalid Telegram update.") from None
    except Exception as exc:
        logger.error("Webhook persistence failed: %s", type(exc).__name__)
        raise HTTPException(503, "Could not store the update. Retry later.") from None
    return {"ok": True}
