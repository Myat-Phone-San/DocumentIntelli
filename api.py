"""Run with python -m uvicorn api:app --host 127.0.0.1 --port 8000."""
from functools import lru_cache
import logging
import os
import secrets
import hashlib
import hmac
from typing import Literal
from uuid import UUID
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.knowledge_base import KnowledgeBase, MongoKnowledgeBase, MAX_UPLOAD_BYTES
from src.settings import LLM_PROVIDER_PRIORITY
from src.conversation_store import ConversationStore

@asynccontextmanager
async def lifespan(app):
    from src import messenger
    webhook = os.getenv("TELEGRAM_MODE", "polling") == "webhook"
    telegram_started = messenger_started = False
    try:
        if webhook:
            from src.telegram_webhook import start, stop
            await start(app)
            telegram_started = True
        if messenger.enabled():
            await messenger.start(app)
            messenger_started = True
        yield
    finally:
        if messenger_started:
            await messenger.stop(app)
        if telegram_started:
            await stop(app)


app = FastAPI(title="Intelligence Chat API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
logger = logging.getLogger(__name__)


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    from src.telegram_webhook import receive
    return await receive(request)


@lru_cache
def get_knowledge_base():
    return MongoKnowledgeBase() if os.getenv("MONGODB_URI", "").strip() else KnowledgeBase()


def require_admin(x_admin_key: str = Header(default="")):
    expected = os.getenv("ADMIN_API_KEY", "").strip()
    if not expected or not secrets.compare_digest(x_admin_key.encode(), expected.encode()):
        raise HTTPException(403, "Administrator access required.")


@app.get("/messenger/webhook")
def messenger_verify(request: Request):
    from src.messenger import verify
    query = request.query_params
    return verify(query.get("hub.mode"), query.get("hub.verify_token"), query.get("hub.challenge"))


@app.post("/messenger/webhook")
async def messenger_receive(request: Request):
    from src.messenger import receive
    return await receive(request)


class MessengerMode(BaseModel):
    mode: Literal["ai", "human"]


def messenger_database():
    from src.messenger import enabled
    if not enabled() or not hasattr(app.state, "messenger_db"):
        raise HTTPException(503, "Messenger is not enabled.")
    return app.state.messenger_db


@app.get("/admin/messenger/threads", dependencies=[Depends(require_admin)])
def messenger_threads():
    from src.mongo_store import public_record
    db = messenger_database()
    return {"threads": [public_record(row) for row in
                        db.messenger_threads.find().sort("updated_at", -1).limit(50)]}


@app.post("/admin/messenger/threads/{psid}/mode", dependencies=[Depends(require_admin)])
def messenger_mode(psid: str, request: MessengerMode):
    from src.messenger import set_mode
    try:
        return set_mode(messenger_database(), psid, request.mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@lru_cache
def get_conversation_store():
    try:
        if os.getenv("MONGODB_URI", "").strip():
            from src.mongo_store import MongoConversationStore
            return MongoConversationStore()
        return ConversationStore()
    except Exception as exc:
        logger.error("Conversation database unavailable: %s", type(exc).__name__)
        raise HTTPException(503, "Conversation database unavailable. Check the database configuration.") from None


def browser_owner(x_anonymous_id: UUID = Header()):
    # Random browser UUID is a bearer credential, not an account login.
    return str(x_anonymous_id)


class SavedChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: int | str | None = None


class TelegramChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    user_id: int = Field(gt=0)


class TelegramIdentity(BaseModel):
    user_id: int = Field(gt=0)


def telegram_owner(identifier):
    # Only the authenticated bot supplies Telegram identity. Web clients cannot
    # derive Telegram history credentials from publicly known numeric user IDs.
    digest = hmac.new(os.environ["ADMIN_API_KEY"].strip().encode(),
                      f"telegram:{identifier}".encode(), hashlib.sha256).digest()
    return str(UUID(bytes=digest[:16]))


def saved_answer(question, identifier, owner, store, kb):
    if not question.strip():
        raise HTTPException(422, "Enter a question.")
    with store.lock(owner):
        if identifier is not None:
            try:
                previous = store.history(owner, identifier, limit=8)
            except KeyError:
                raise HTTPException(404, "Conversation not found.") from None
        else:
            previous = []
        try:
            context = [{"role": item["role"], "content": item["content"][:12000]} for item in previous]
            result = kb.answer(question.strip(), context)
            if identifier is None:
                identifier = store.create(owner)
            store.append_turn(owner, identifier, question.strip(), result["answer"])
            return {**result, "conversation_id": identifier,
                    "messages": store.history(owner, identifier, limit=2)}
        except Exception as exc:
            logger.error("Persistent chat failed: %s", type(exc).__name__)
            raise HTTPException(503, "Could not complete and save the answer. Please try again.") from None


@app.get("/conversations")
def list_conversations(owner=Depends(browser_owner), store=Depends(get_conversation_store)):
    return {"conversations": store.list(owner)}


@app.get("/conversations/{identifier}")
def conversation_messages(identifier: str, before: str | None = None,
                          owner=Depends(browser_owner), store=Depends(get_conversation_store)):
    try:
        history = store.history(owner, identifier, before=before)
    except KeyError:
        raise HTTPException(404, "Conversation not found.") from None
    return {"messages": history, "next_before": history[0]["id"] if len(history) == 200 else None}


@app.delete("/conversations/{identifier}")
def delete_conversation(identifier: str, owner=Depends(browser_owner), store=Depends(get_conversation_store)):
    try:
        store.remove(owner, identifier)
    except KeyError:
        raise HTTPException(404, "Conversation not found.") from None
    return {"deleted": True}


@app.post("/conversations/chat")
def persistent_chat(request: SavedChatRequest, owner=Depends(browser_owner),
                    store=Depends(get_conversation_store), kb=Depends(get_knowledge_base)):
    return saved_answer(request.question, request.conversation_id, owner, store, kb)


@app.post("/telegram/chat", dependencies=[Depends(require_admin)])
def telegram_chat(request: TelegramChatRequest, store=Depends(get_conversation_store), kb=Depends(get_knowledge_base)):
    owner = telegram_owner(request.user_id)
    with store.lock(owner):
        return saved_answer(request.question, store.latest(owner), owner, store, kb)


@app.post("/telegram/new", dependencies=[Depends(require_admin)])
def telegram_new(request: TelegramIdentity, store=Depends(get_conversation_store)):
    return {"conversation_id": store.create(telegram_owner(request.user_id))}


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[Message] = Field(default_factory=list, max_length=8)


@app.get("/health")
def health():
    return {"status": "ok", "provider_order": LLM_PROVIDER_PRIORITY}


@app.post("/chat")
def chat(request: ChatRequest, kb=Depends(get_knowledge_base)):
    if not request.question.strip():
        raise HTTPException(422, "Enter a question.")
    try:
        return kb.answer(request.question.strip(), [item.model_dump() for item in request.history])
    except Exception as exc:
        logger.error("Chat failed: %s", type(exc).__name__)
        raise HTTPException(503, "The assistant is temporarily unavailable. Please try again.") from None


@app.get("/admin/documents", dependencies=[Depends(require_admin)])
def documents(kb=Depends(get_knowledge_base)):
    return {"documents": kb.list_documents()}


@app.post("/admin/documents", dependencies=[Depends(require_admin)])
async def upload(file: UploadFile = File(...), category: str = Form("General"),
                 kb=Depends(get_knowledge_base)):
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        return await run_in_threadpool(kb.add, file.filename or "document", content, category)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:
        logger.error("Upload failed: %s", type(exc).__name__)
        raise HTTPException(503, "Document could not be indexed. Check backend logs and retry.") from None
    finally:
        await file.close()


@app.delete("/admin/documents/{identifier}", dependencies=[Depends(require_admin)])
def remove(identifier: str, kb=Depends(get_knowledge_base)):
    try:
        kb.delete(identifier)
    except KeyError:
        raise HTTPException(404, "Document not found.") from None
    except Exception as exc:
        logger.error("Delete failed: %s", type(exc).__name__)
        raise HTTPException(503, "Document could not be deleted. Please retry.") from None
    return {"deleted": True}
