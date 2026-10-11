"""Telegram transport. The API process owns the shared knowledge base."""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from time import monotonic

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from src.knowledge_base import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES
from src.settings import ROOT_DIR  # Loads .env before reading configuration.

ADMIN_IDS = {int(value.strip()) for value in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") if value.strip()}
API_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "").strip()
KB_BUTTON = "📚 Knowledge base"
UPLOAD_BUTTON = "📤 Upload document"
HELP_BUTTON = "❓ Help"
logger = logging.getLogger(__name__)
last_question = {}
locks = defaultdict(asyncio.Lock)


def is_admin(update):
    return bool(update.effective_user and update.effective_user.id in ADMIN_IDS
                and update.effective_chat and update.effective_chat.type == "private")


async def api_call(method, path, **kwargs):
    async with httpx.AsyncClient(timeout=240) as client:
        response = await client.request(method, API_URL + path, **kwargs)
        if response.is_error:
            detail = response.json().get("detail", "Backend request failed.")
            raise RuntimeError(detail if isinstance(detail, str) else "Invalid request.")
        return response.json()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Please message me privately to ask questions.")
        return
    text = "Hello! Ask me a question and I will answer using the approved documents. Use /new to start a fresh conversation."
    kwargs = {}
    if is_admin(update):
        text += "\n\nAdministrator access: upload documents or manage the knowledge base below."
        kwargs["reply_markup"] = ReplyKeyboardMarkup(
            [[KB_BUTTON, UPLOAD_BUTTON], [HELP_BUTTON]], resize_keyboard=True)
    else:
        from telegram import ReplyKeyboardRemove
        kwargs["reply_markup"] = ReplyKeyboardRemove()
    await update.effective_message.reply_text(text, **kwargs)


async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    identifier = update.effective_user.id
    if locks[identifier].locked():
        await update.effective_message.reply_text("Please wait for the current answer, then use /new.")
        return
    await api_call("POST", "/telegram/new", headers={"X-Admin-Key": ADMIN_KEY}, json={"user_id": identifier})
    await update.effective_message.reply_text("New conversation started. What would you like to ask?")


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.effective_message.reply_text(f"Your Telegram user ID: {update.effective_user.id}")


async def show_kb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text("Administrator access required.")
        return
    result = await api_call("GET", "/admin/documents", headers={"X-Admin-Key": ADMIN_KEY})
    documents = result["documents"]
    groups = {}
    for document in documents:
        groups.setdefault(document["category"], []).append(document)
    await update.effective_message.reply_text(
        f"Knowledge base · {len(documents)} documents. Uploads persist across restarts."
        if documents else "Knowledge base is empty. Send a supported document to add it.")
    # Paginate to keep Telegram keyboard and message sizes bounded.
    for category, entries in groups.items():
        for offset in range(0, len(entries), 10):
            page = entries[offset:offset + 10]
            keyboard = [[InlineKeyboardButton(f"📄 {item['name'][:55]}", callback_data=f"doc:{item['id']}")]
                        for item in page]
            await update.effective_message.reply_text(category, reply_markup=InlineKeyboardMarkup(keyboard))


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update):
        await query.answer("Administrator access required.", show_alert=True)
        return
    await query.answer()
    action, _, identifier = (query.data or "").partition(":")
    if action == "delete":
        await api_call("DELETE", f"/admin/documents/{identifier}", headers={"X-Admin-Key": ADMIN_KEY})
        await query.edit_message_text("Document deleted from the knowledge base.")
        return
    documents = (await api_call("GET", "/admin/documents", headers={"X-Admin-Key": ADMIN_KEY}))["documents"]
    document = next((item for item in documents if item["id"] == identifier), None)
    if document is None:
        await query.edit_message_text("This document is no longer available.")
        return
    keyboard = [[InlineKeyboardButton("Confirm deletion", callback_data=f"delete:{identifier}"),
                 InlineKeyboardButton("Keep document", callback_data=f"keep:{identifier}")]] if action == "confirm" else [
                     [InlineKeyboardButton("Delete document", callback_data=f"confirm:{identifier}")]]
    await query.edit_message_text(
        f"{document['name']}\nGroup: {document['category']}\nAdded: {document['created_at']}"
        + ("\nDelete this document permanently?" if action == "confirm" else ""),
        reply_markup=InlineKeyboardMarkup(keyboard))


async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text("Only administrators can upload documents. Please send a question instead.")
        return
    from pathlib import Path
    document = update.effective_message.document
    filename = document.file_name or "document"
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        await update.effective_message.reply_text("Supported files: PDF, DOCX, XLSX, TXT, MD, CSV.")
        return
    if (document.file_size or 0) > MAX_UPLOAD_BYTES:
        await update.effective_message.reply_text("The maximum file size is 20 MB.")
        return
    status = await update.effective_message.reply_text("Reading and indexing your document…")
    remote = await document.get_file()
    content = bytes(await remote.download_as_bytearray())
    category = (update.effective_message.caption or "General").strip()[:80]
    result = await api_call("POST", "/admin/documents", headers={"X-Admin-Key": ADMIN_KEY},
                            files={"file": (filename, content)}, data={"category": category})
    await status.edit_text(f"Added {result['name']} to {result['category']}. Users can now ask questions about it.")


async def text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    question = update.effective_message.text.strip()
    if question == KB_BUTTON:
        await show_kb(update, context)
        return
    if question == UPLOAD_BUTTON:
        await update.effective_message.reply_text(
            "Send a document. Add a caption to choose its knowledge-base group (for example, Accounts)."
            if is_admin(update) else "Administrator access required.")
        return
    if question == HELP_BUTTON:
        await start(update, context)
        return
    if len(question) > 4000:
        await update.effective_message.reply_text("Please keep questions under 4,000 characters.")
        return
    identifier = update.effective_user.id
    for stale, timestamp in list(last_question.items()):
        if monotonic() - timestamp > 3600 and not locks[stale].locked():
            last_question.pop(stale, None)
            locks.pop(stale, None)
    if monotonic() - last_question.get(identifier, 0) < 3 or locks[identifier].locked():
        await update.effective_message.reply_text("Please wait for the current answer before asking again.")
        return
    last_question[identifier] = monotonic()
    async with locks[identifier]:
        await context.bot.send_chat_action(update.effective_chat.id, "typing")
        result = await api_call("POST", "/telegram/chat", headers={"X-Admin-Key": ADMIN_KEY},
                                json={"question": question, "user_id": identifier})
        answer = result["answer"]
        # Plain text avoids failures caused by LLM-generated Markdown escaping.
        for offset in range(0, len(answer), 3500):
            await update.effective_message.reply_text(answer[offset:offset + 3500])


async def error(update: object, context: ContextTypes.DEFAULT_TYPE):
    # HTTP exceptions may include the bot token in their URL. Never log exception bodies.
    logger.error("Telegram handler failed: %s", type(context.error).__name__)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("The request could not be completed. Check that the backend is running and try again.")


def build_application():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not ADMIN_KEY:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and ADMIN_API_KEY in .env first.")
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not ADMIN_IDS:
        logger.warning("No Telegram admins configured. Use /whoami in a private chat, add the ID to TELEGRAM_ADMIN_IDS, and restart.")
    application = Application.builder().token(token).concurrent_updates(8).build()
    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(CommandHandler("new", new_chat))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("kb", show_kb))
    application.add_handler(CallbackQueryHandler(callback, pattern=r"^(doc|confirm|delete|keep):[a-f0-9]{32}$"))
    application.add_handler(MessageHandler(filters.Document.ALL, upload))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    application.add_error_handler(error)
    return application


def main():
    if os.getenv("TELEGRAM_MODE", "polling") == "webhook":
        raise SystemExit("Webhook mode runs inside the API. Start uvicorn; do not start a polling bot too.")
    build_application().run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)


if __name__ == "__main__":
    main()
