"""Signed Messenger webhooks and durable AI/human conversation routing.

Run one API worker/instance. Human replies are sent from Meta Business Suite.
"""
import asyncio
from contextlib import suppress
from datetime import timedelta
import hashlib
import hmac
import json
import logging
import os
import re

import httpx
from fastapi import HTTPException
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from src.mongo_store import get_database, now

logger = logging.getLogger(__name__)
HUMAN_PAYLOAD = "HUMAN_SUPPORT"
HUMAN_TEXT = {"human", "human support", "talk to a person", "ဝန်ထမ်းနှင့်ပြောမည်"}


def enabled():
    return os.getenv("MESSENGER_ENABLED", "false").lower() == "true"


def verify(mode, token, challenge):
    expected = os.getenv("MESSENGER_VERIFY_TOKEN", "").strip()
    if not enabled() or not expected:
        raise HTTPException(503, "Messenger is not enabled.")
    if mode != "subscribe" or not hmac.compare_digest(token or "", expected) or not challenge:
        raise HTTPException(403, "Invalid verification token.")
    return PlainTextResponse(challenge)


def set_mode(db, psid, mode):
    if not re.fullmatch(r"[0-9]{1,40}", psid):
        raise ValueError("Enter a valid Page-scoped user ID.")
    if mode not in {"ai", "human"}:
        raise ValueError("Mode must be ai or human.")
    db.messenger_threads.update_one({"_id": psid}, {
        "$set": {"mode": mode, "updated_at": now()},
        "$inc": {"revision": 1}}, upsert=True)
    return {"psid": psid, "mode": mode}


def ingest(db, payload):
    page = os.environ["MESSENGER_PAGE_ID"]
    if not isinstance(payload, dict) or payload.get("object") != "page":
        raise ValueError("Expected a Page webhook.")
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        raise ValueError("Invalid entries.")
    for entry in entries:
        if str(entry.get("id")) != page:
            continue
        for event in entry.get("messaging", []):
            message = event.get("message", {})
            echo = message.get("is_echo", False)
            psid = str(event.get("recipient" if echo else "sender", {}).get("id", ""))
            if not re.fullmatch(r"[0-9]{1,40}", psid) or psid == page:
                continue
            if not echo and str(event.get("recipient", {}).get("id")) != page:
                continue
            postback = event.get("postback", {})
            if not message and not postback:
                continue
            mid = message.get("mid") or postback.get("mid")
            key = str(mid) if mid else hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
            # Meta retries use the same key. Completed records expire after 7 days.
            result = db.messenger_inbox.update_one({"_id": key}, {"$setOnInsert": {
                "payload": event, "psid": psid, "state": "pending", "received_at": now()}}, upsert=True)
            if result.upserted_id is None:
                continue
            if echo:
                # Our own Send API echoes must never pause the AI or trigger a reply.
                if str(message.get("app_id", "")) != os.environ["MESSENGER_APP_ID"]:
                    set_mode(db, psid, "human")
                db.messenger_inbox.update_one({"_id": key}, {"$set": {
                    "state": "done", "expires_at": now() + timedelta(days=7)}})
                continue
            thread = db.messenger_threads.find_one({"_id": psid})
            if thread is None:
                db.messenger_threads.update_one({"_id": psid}, {"$setOnInsert": {
                    "mode": "ai", "revision": 0}}, upsert=True)
            thread = db.messenger_threads.find_one({"_id": psid})
            db.messenger_inbox.update_one({"_id": key}, {"$set": {
                "received_revision": thread.get("revision", 0)}})
            timestamp = event.get("timestamp")
            # Use Meta's event timestamp so delayed/replayed events don't reopen the window.
            if isinstance(timestamp, (int, float)):
                from datetime import datetime, timezone
                contact = min(datetime.fromtimestamp(timestamp / 1000, timezone.utc), now())
                db.messenger_threads.update_one({"_id": psid}, {"$max": {"last_contact": contact}})
            text = message.get("text", "")
            command = message.get("quick_reply", {}).get("payload") or postback.get("payload")
            if command == HUMAN_PAYLOAD or (isinstance(text, str) and text.strip().casefold() in HUMAN_TEXT):
                set_mode(db, psid, "human")
                db.messenger_inbox.update_one({"_id": key}, {"$set": {"human_request": True}})


async def receive(request):
    if not enabled() or not hasattr(request.app.state, "messenger_db"):
        raise HTTPException(503, "Messenger is not enabled.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            raise HTTPException(413, "Webhook too large.")
    signature = "sha256=" + hmac.new(os.environ["MESSENGER_APP_SECRET"].encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("x-hub-signature-256", ""), signature):
        raise HTTPException(403, "Invalid webhook signature.")
    try:
        await run_in_threadpool(ingest, request.app.state.messenger_db, json.loads(body))
    except (ValueError, TypeError, AttributeError, KeyError):
        raise HTTPException(400, "Invalid webhook payload.") from None
    except Exception as exc:
        logger.error("Messenger persistence failed: %s", type(exc).__name__)
        raise HTTPException(503, "Could not save update. Retry later.") from None
    return PlainTextResponse("EVENT_RECEIVED")


async def send(psid, text, offer_human=True):
    version = os.environ["MESSENGER_GRAPH_VERSION"]
    page = os.environ["MESSENGER_PAGE_ID"]
    message = {"text": text[:1900]}
    if offer_human:
        message["quick_replies"] = [{"content_type": "text", "title": "Talk to a person", "payload": HUMAN_PAYLOAD}]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"https://graph.facebook.com/{version}/{page}/messages",
            headers={"Authorization": "Bearer " + os.environ["MESSENGER_PAGE_ACCESS_TOKEN"]},
            json={"recipient": {"id": psid}, "messaging_type": "RESPONSE", "message": message})
        if response.is_error:
            # Never log tokens, user text, or provider response bodies.
            raise RuntimeError(f"Messenger Send API HTTP {response.status_code}")


def claim(db):
    from pymongo import ReturnDocument
    timestamp = now()
    return db.messenger_inbox.find_one_and_update({"$or": [
        {"state": "pending", "retry_at": {"$exists": False}},
        {"state": "pending", "retry_at": {"$lte": timestamp}},
        {"state": "processing", "lease_until": {"$lt": timestamp}}]},
        {"$set": {"state": "processing", "lease_until": timestamp + timedelta(minutes=15)},
         "$inc": {"attempts": 1}}, sort=[("received_at", 1)], return_document=ReturnDocument.AFTER)


def can_reply(thread):
    return bool(thread and thread.get("last_contact") and
                now() - thread["last_contact"] < timedelta(hours=24))


async def handle(db, item):
    psid = item["psid"]
    event = item["payload"]
    message = event.get("message", {})
    if message.get("is_echo"):
        if str(message.get("app_id", "")) != os.environ["MESSENGER_APP_ID"]:
            await run_in_threadpool(set_mode, db, psid, "human")
        return
    command = message.get("quick_reply", {}).get("payload") or event.get("postback", {}).get("payload")
    text = message.get("text", "")
    human_request = item.get("human_request") or command == HUMAN_PAYLOAD or (
        isinstance(text, str) and text.strip().casefold() in HUMAN_TEXT)
    thread = await run_in_threadpool(db.messenger_threads.find_one, {"_id": psid})
    if human_request and thread and thread.get("mode") != "human":
        await run_in_threadpool(set_mode, db, psid, "human")
    if not can_reply(thread):
        return
    if human_request:
        await send(psid, "Your request for human support has been recorded. AI replies are paused. A team member can reply here when available.", False)
        return
    if thread.get("mode") != "ai":
        return
    if not isinstance(text, str) or not text.strip():
        await send(psid, "I am an AI document assistant. Please send a text question, or choose Talk to a person.")
        return
    if len(text) > 4000:
        await send(psid, "Please keep your question under 4,000 characters.")
        return
    revision = item.get("answer_revision", item.get("received_revision", thread.get("revision", 0)))
    if revision != thread.get("revision", 0):
        return
    from api import get_knowledge_base
    def answer():
        rows = list(db.messenger_history.find({"psid": psid}).sort("created_at", -1).limit(4))
        history = [turn for row in reversed(rows) for turn in (
            {"role": "user", "content": row["question"]}, {"role": "assistant", "content": row["answer"]})]
        return get_knowledge_base().answer(text.strip(), history)["answer"]
    reply = item.get("answer")
    if reply is None:
        reply = await run_in_threadpool(answer)
        await run_in_threadpool(db.messenger_inbox.update_one, {"_id": item["_id"]},
                                {"$set": {"answer": reply, "answer_revision": revision}})
    for offset in range(item.get("sent_offset", 0), len(reply), 1900):
        current = await run_in_threadpool(db.messenger_threads.find_one, {"_id": psid})
        if not can_reply(current) or current.get("mode") != "ai" or current.get("revision", 0) != revision:
            return
        await send(psid, reply[offset:offset + 1900])
        await run_in_threadpool(db.messenger_inbox.update_one, {"_id": item["_id"]}, {"$set": {"sent_offset": offset + 1900}})
    await run_in_threadpool(db.messenger_history.update_one, {"_id": item["_id"]}, {"$setOnInsert": {
        "psid": psid, "question": text, "answer": reply, "created_at": now()}}, upsert=True)


async def worker(db):
    while True:
        item = None
        try:
            item = await run_in_threadpool(claim, db)
            if item is None:
                await asyncio.sleep(1)
                continue
            await handle(db, item)
            await run_in_threadpool(db.messenger_inbox.update_one, {"_id": item["_id"]}, {"$set": {
                "state": "done", "expires_at": now() + timedelta(days=7)}})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Messenger processing failed: %s", type(exc).__name__)
            if item:
                failed = item.get("attempts", 1) >= 5
                await run_in_threadpool(db.messenger_inbox.update_one, {"_id": item["_id"]}, {"$set": {
                    "state": "failed" if failed else "pending", "error_type": type(exc).__name__,
                    "retry_at": now() + timedelta(seconds=30)}})
            await asyncio.sleep(5)


async def start(app):
    required = ("MONGODB_URI", "MESSENGER_PAGE_ID", "MESSENGER_APP_ID", "MESSENGER_APP_SECRET",
                "MESSENGER_PAGE_ACCESS_TOKEN", "MESSENGER_VERIFY_TOKEN", "MESSENGER_GRAPH_VERSION")
    if any(not os.getenv(key, "").strip() for key in required):
        raise RuntimeError("Set all Messenger environment variables before enabling Messenger.")
    if not re.fullmatch(r"v\d+\.\d+", os.environ["MESSENGER_GRAPH_VERSION"]):
        raise RuntimeError("MESSENGER_GRAPH_VERSION must look like vXX.0.")
    db = await run_in_threadpool(get_database)
    def indexes():
        db.messenger_inbox.create_index([("state", 1), ("received_at", 1)])
        db.messenger_inbox.create_index("expires_at", expireAfterSeconds=0)
        db.messenger_history.create_index([("psid", 1), ("created_at", -1)])
    await run_in_threadpool(indexes)
    app.state.messenger_db = db
    app.state.messenger_worker = asyncio.create_task(worker(db))


async def stop(app):
    app.state.messenger_worker.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.messenger_worker
