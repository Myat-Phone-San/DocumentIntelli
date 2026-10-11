"""MongoDB Atlas persistence. Never expose the MongoDB URI to browsers."""
from datetime import datetime, timezone
from functools import lru_cache
import os
from threading import RLock
from uuid import uuid4

from pymongo import MongoClient

from src.settings import ROOT_DIR  # Load local .env.


def now():
    return datetime.now(timezone.utc)


def public_record(record):
    return {("id" if key == "_id" else key): value.isoformat().replace("+00:00", "Z")
            if isinstance(value, datetime) else value for key, value in record.items()
            if key not in {"anonymous_id", "message_count"}}


@lru_cache
def get_database():
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        raise ValueError("Set MONGODB_URI before selecting MongoDB.")
    client = MongoClient(uri, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000,
                         maxPoolSize=20, tz_aware=True)
    client.admin.command("ping")
    return client[os.getenv("MONGODB_DATABASE", "banking_chat")]


def ensure_indexes(database):
    database.conversations.create_index([("anonymous_id", 1), ("updated_at", -1), ("_id", -1)])
    database.messages.create_index([("conversation_id", 1), ("sequence", 1)], unique=True)
    database.managed_chunks.create_index("document_id")
    database.managed_chunks.create_index("id", unique=True)
    database.telegram_inbox.create_index([("state", 1), ("lease_until", 1)])
    database.telegram_inbox.create_index("expires_at", expireAfterSeconds=0)


class MongoConversationStore:
    def __init__(self, database=None):
        self.db = database if database is not None else get_database()
        ensure_indexes(self.db)
        self.locks = [RLock() for _ in range(64)]

    def lock(self, owner):
        return self.locks[hash(owner) % len(self.locks)]

    def _transaction(self, operation):
        with self.db.client.start_session() as session:
            return session.with_transaction(operation)

    def _owned(self, owner, identifier, session=None):
        record = self.db.conversations.find_one({"_id": str(identifier), "anonymous_id": owner}, session=session)
        if record is None:
            raise KeyError(identifier)
        return record

    def create(self, owner):
        identifier = uuid4().hex
        timestamp = now()
        def operation(session):
            self.db.conversations.insert_one({"_id": identifier, "anonymous_id": owner,
                "title": "New conversation", "created_at": timestamp, "updated_at": timestamp,
                "message_count": 0}, session=session)
            self.db.conversation_heads.update_one({"_id": owner},
                {"$set": {"conversation_id": identifier}}, upsert=True, session=session)
        with self.lock(owner):
            self._transaction(operation)
        return identifier

    def latest(self, owner):
        head = self.db.conversation_heads.find_one({"_id": owner})
        if head and self.db.conversations.find_one({"_id": head["conversation_id"], "anonymous_id": owner}):
            return head["conversation_id"]
        record = self.db.conversations.find_one({"anonymous_id": owner}, sort=[("updated_at", -1), ("_id", -1)])
        return record["_id"] if record else None

    def list(self, owner):
        return [public_record(item) for item in self.db.conversations.find({"anonymous_id": owner})
                .sort([("updated_at", -1), ("_id", -1)]).limit(50)]

    def history(self, owner, identifier, limit=200, before=None):
        self._owned(owner, identifier)
        query = {"conversation_id": str(identifier)}
        if before is not None:
            cursor = self.db.messages.find_one({**query, "_id": str(before)})
            if cursor is None:
                raise KeyError(before)
            query["sequence"] = {"$lt": cursor["sequence"]}
        rows = list(self.db.messages.find(query).sort("sequence", -1).limit(limit))
        return [public_record(item) for item in reversed(rows)]

    def append_turn(self, owner, identifier, question, answer):
        ids = [uuid4().hex, uuid4().hex]
        timestamp = now()
        def operation(session):
            record = self._owned(owner, identifier, session)
            sequence = record.get("message_count", 0)
            self.db.messages.insert_many([
                {"_id": message_id, "conversation_id": str(identifier), "sequence": sequence + offset,
                 "role": role, "content": content, "sources": None, "retrieved": None,
                 "follow_up": None, "created_at": timestamp}
                for offset, (message_id, role, content) in enumerate(
                    ((ids[0], "user", question), (ids[1], "assistant", answer)), start=1)
            ], session=session)
            self.db.conversations.update_one({"_id": str(identifier)}, {"$set": {
                "updated_at": timestamp, "title": question[:255] if sequence == 0 else record["title"]},
                "$inc": {"message_count": 2}}, session=session)
            self.db.conversation_heads.update_one({"_id": owner},
                {"$set": {"conversation_id": str(identifier)}}, upsert=True, session=session)
            return ids
        with self.lock(owner):
            return self._transaction(operation)

    def remove(self, owner, identifier):
        def operation(session):
            self._owned(owner, identifier, session)
            self.db.messages.delete_many({"conversation_id": str(identifier)}, session=session)
            self.db.conversations.delete_one({"_id": str(identifier)}, session=session)
            self.db.conversation_heads.delete_one({"_id": owner, "conversation_id": str(identifier)}, session=session)
        with self.lock(owner):
            self._transaction(operation)
