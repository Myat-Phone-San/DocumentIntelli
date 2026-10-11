"""SQL conversation history: SQLite locally, MySQL/MariaDB with XAMPP."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from threading import RLock

from sqlalchemy import (Column, ForeignKey, Index, Integer, JSON, MetaData, String,
                        Table, Text, create_engine, delete, event, insert, select, update)
from sqlalchemy.dialects.mysql import INTEGER, MEDIUMTEXT, TIMESTAMP, ENUM
from sqlalchemy.engine import URL
from sqlalchemy.types import DateTime

from src.settings import DATA_DIR

metadata = MetaData()
identifier_type = Integer().with_variant(INTEGER(unsigned=True), "mysql")
timestamp_type = DateTime().with_variant(TIMESTAMP(), "mysql")
conversations = Table("conversations", metadata,
    Column("id", identifier_type, primary_key=True, autoincrement=True),
    Column("anonymous_id", String(36), nullable=False),
    Column("title", String(255), nullable=False, default="New conversation"),
    Column("created_at", timestamp_type, nullable=False),
    Column("updated_at", timestamp_type, nullable=False),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_bin")
Index("idx_conversations_anonymous_id_updated_at", conversations.c.anonymous_id, conversations.c.updated_at)
messages = Table("messages", metadata,
    Column("id", identifier_type, primary_key=True, autoincrement=True),
    Column("conversation_id", identifier_type, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
    Column("role", String(16).with_variant(ENUM("user", "assistant"), "mysql"), nullable=False),
    Column("content", Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
    Column("sources", JSON, nullable=True), Column("retrieved", JSON, nullable=True),
    Column("follow_up", String(500), nullable=True),
    Column("created_at", timestamp_type, nullable=False),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_bin")
Index("idx_messages_conversation_id", messages.c.conversation_id)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def serialize(row):
    return {key: value.isoformat() + "Z" if isinstance(value, datetime) else value
            for key, value in dict(row).items()}


def configured_engine():
    backend = os.getenv("CHAT_DB_BACKEND", "sqlite").strip().lower()
    if backend == "sqlite":
        directory = DATA_DIR / "managed"
        directory.mkdir(parents=True, exist_ok=True)
        return create_engine(URL.create("sqlite", database=str(directory / "conversations.sqlite3")),
                             connect_args={"check_same_thread": False, "timeout": 30})
    if backend != "mysql":
        raise ValueError("CHAT_DB_BACKEND must be sqlite or mysql.")
    return create_engine(URL.create("mysql+pymysql",
        username=os.getenv("CHAT_DB_USER", "root"), password=os.getenv("CHAT_DB_PASSWORD", ""),
        host=os.getenv("CHAT_DB_HOST", "127.0.0.1"), port=int(os.getenv("CHAT_DB_PORT", "3306")),
        database=os.getenv("CHAT_DB_NAME", "banking_chat"), query={"charset": "utf8mb4"}),
        pool_pre_ping=True, connect_args={"connect_timeout": 5})


class ConversationStore:
    def __init__(self, engine=None):
        self.engine = engine if engine is not None else configured_engine()
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def foreign_keys(connection, record):
                connection.execute("PRAGMA foreign_keys=ON")
        metadata.create_all(self.engine)
        # The existing backend runs one worker. Bound locks avoid per-user memory growth.
        self.locks = [RLock() for _ in range(64)]

    def lock(self, owner):
        return self.locks[hash(owner) % len(self.locks)]

    def _owned(self, connection, owner, identifier):
        row = connection.execute(select(conversations).where(
            conversations.c.id == identifier, conversations.c.anonymous_id == owner)).mappings().first()
        if row is None:
            raise KeyError(identifier)
        return row

    def create(self, owner):
        with self.lock(owner), self.engine.begin() as connection:
            now = utcnow()
            result = connection.execute(insert(conversations).values(
                anonymous_id=owner, title="New conversation", created_at=now, updated_at=now))
            return result.inserted_primary_key[0]

    def latest(self, owner):
        with self.engine.connect() as connection:
            return connection.execute(select(conversations.c.id).where(
                conversations.c.anonymous_id == owner).order_by(
                    conversations.c.updated_at.desc(), conversations.c.id.desc()).limit(1)).scalar_one_or_none()

    def list(self, owner):
        with self.engine.connect() as connection:
            rows = connection.execute(select(conversations.c.id, conversations.c.title,
                conversations.c.created_at, conversations.c.updated_at).where(
                    conversations.c.anonymous_id == owner).order_by(
                        conversations.c.updated_at.desc(), conversations.c.id.desc()).limit(50)).mappings()
            return [serialize(row) for row in rows]

    def history(self, owner, identifier, limit=200, before=None):
        with self.engine.connect() as connection:
            self._owned(connection, owner, identifier)
            query = select(messages).where(messages.c.conversation_id == identifier)
            if before is not None:
                query = query.where(messages.c.id < before)
            rows = connection.execute(query.order_by(messages.c.id.desc()).limit(limit)).mappings().all()
            return [serialize(row) for row in reversed(rows)]

    def append_turn(self, owner, identifier, question, answer):
        with self.lock(owner), self.engine.begin() as connection:
            row = self._owned(connection, owner, identifier)
            now = utcnow()
            ids = []
            for role, content in (("user", question), ("assistant", answer)):
                result = connection.execute(insert(messages).values(conversation_id=identifier,
                    role=role, content=content, created_at=now))
                ids.append(result.inserted_primary_key[0])
            connection.execute(update(conversations).where(conversations.c.id == identifier).values(
                updated_at=now, title=question[:255] if row["title"] == "New conversation" else row["title"]))
            return ids

    def remove(self, owner, identifier):
        with self.lock(owner), self.engine.begin() as connection:
            self._owned(connection, owner, identifier)
            connection.execute(delete(conversations).where(conversations.c.id == identifier))


if __name__ == "__main__":
    store = ConversationStore()
    print(f"Conversation tables ready ({store.engine.dialect.name}).")
    store.engine.dispose()
