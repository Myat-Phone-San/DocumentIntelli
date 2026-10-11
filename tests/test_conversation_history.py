import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from uuid import uuid4

import mongomock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

import api
from src.conversation_store import ConversationStore, messages
from src.mongo_store import MongoConversationStore
from src.pipeline.lexical_index import LexicalIndex


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'history.sqlite3'}",
                               connect_args={"check_same_thread": False})
        self.sql = ConversationStore(engine)
        self.mongo = MongoConversationStore(mongomock.MongoClient(tz_aware=True).test_history)
        # mongomock does not implement sessions; real Atlas uses with_transaction.
        self.mongo._transaction = lambda operation: operation(None)
        self.owner, self.other = str(uuid4()), str(uuid4())

    def tearDown(self):
        api.app.dependency_overrides.clear()
        self.sql.engine.dispose()
        self.directory.cleanup()

    def test_each_store_persists_orders_and_scopes_messages(self):
        for store in (self.sql, self.mongo):
            with self.subTest(store=type(store).__name__):
                identifier = store.create(self.owner)
                store.append_turn(self.owner, identifier, "First question", "First answer")
                store.append_turn(self.owner, identifier, "Follow-up", "Second answer")
                self.assertEqual([item["role"] for item in store.history(self.owner, identifier)],
                                 ["user", "assistant", "user", "assistant"])
                self.assertTrue(store.history(self.owner, identifier)[0]["created_at"])
                self.assertEqual(store.list(self.other), [])
                with self.assertRaises(KeyError):
                    store.history(self.other, identifier)
                with self.assertRaises(KeyError):
                    store.remove(self.other, identifier)
                recent = store.history(self.owner, identifier, limit=2)
                older = store.history(self.owner, identifier, before=recent[0]["id"])
                self.assertEqual([item["content"] for item in older], ["First question", "First answer"])
                store.remove(self.owner, identifier)
                self.assertEqual(store.list(self.owner), [])
                if store is self.mongo:
                    self.assertEqual(store.db.messages.count_documents({"conversation_id": identifier}), 0)
                else:
                    with store.engine.connect() as connection:
                        self.assertEqual(connection.execute(select(messages)).all(), [])

    def test_restart_retains_history(self):
        identifier = self.sql.create(self.owner)
        self.sql.append_turn(self.owner, identifier, "Question", "Answer")
        restarted = ConversationStore(self.sql.engine)
        self.assertEqual(restarted.history(self.owner, identifier)[-1]["content"], "Answer")
        identifier = self.mongo.create(self.owner)
        self.mongo.append_turn(self.owner, identifier, "Question", "Answer")
        self.assertEqual(MongoConversationStore(self.mongo.db).history(self.owner, identifier)[-1]["content"], "Answer")

    def test_api_saves_turn_and_uses_server_history(self):
        for store in (self.sql, self.mongo):
            api.app.dependency_overrides[api.get_conversation_store] = lambda: store
            seen = []
            def answer(question, previous):
                seen.append(previous)
                return {"answer": "Approved answer"}
            api.app.dependency_overrides[api.get_knowledge_base] = lambda: SimpleNamespace(answer=answer)
            client = TestClient(api.app)
            headers = {"X-Anonymous-Id": self.owner}
            result = client.post("/conversations/chat", headers=headers, json={"question": "Hello"})
            self.assertEqual(result.status_code, 200, result.text)
            identifier = result.json()["conversation_id"]
            result = client.post("/conversations/chat", headers=headers,
                json={"question": "Follow-up", "conversation_id": identifier})
            self.assertEqual(len(seen[-1]), 2)
            self.assertEqual(len(client.get(f"/conversations/{identifier}", headers=headers).json()["messages"]), 4)
            outsider = {"X-Anonymous-Id": self.other}
            self.assertEqual(client.get(f"/conversations/{identifier}", headers=outsider).status_code, 404)
            self.assertEqual(client.post("/conversations/chat", headers=outsider,
                json={"question": "steal", "conversation_id": identifier}).status_code, 404)
            self.assertEqual(client.delete(f"/conversations/{identifier}", headers=outsider).status_code, 404)
            self.assertEqual(client.get("/conversations").status_code, 422)

    def test_telegram_identity_is_authenticated_and_restart_safe(self):
        api.app.dependency_overrides[api.get_conversation_store] = lambda: self.mongo
        seen = []
        def answer(question, previous):
            seen.append(previous)
            return {"answer": "Hello"}
        api.app.dependency_overrides[api.get_knowledge_base] = lambda: SimpleNamespace(answer=answer)
        client = TestClient(api.app)
        with patch.dict(os.environ, {"ADMIN_API_KEY": "stable-test-key"}):
            self.assertEqual(client.post("/telegram/chat", json={"user_id": 10, "question": "Hi"}).status_code, 403)
            headers = {"X-Admin-Key": "stable-test-key"}
            first = client.post("/telegram/chat", headers=headers, json={"user_id": 10, "question": "Hi"}).json()
            second = client.post("/telegram/chat", headers=headers, json={"user_id": 10, "question": "Again"}).json()
            self.assertEqual(first["conversation_id"], second["conversation_id"])
            self.assertEqual(len(seen[-1]), 2)
            client.post("/telegram/chat", headers=headers, json={"user_id": 20, "question": "Hi"})
            self.assertEqual(seen[-1], [])
            client.post("/telegram/new", headers=headers, json={"user_id": 10})
            client.post("/telegram/chat", headers=headers, json={"user_id": 10, "question": "New"})
            self.assertEqual(seen[-1], [])

    def test_mongodb_active_conversation_handles_equal_timestamps(self):
        from src.mongo_store import now
        with patch("src.mongo_store.now", return_value=now()):
            identifiers = [self.mongo.create(self.owner) for _ in range(10)]
        self.assertEqual(self.mongo.latest(self.owner), identifiers[-1])

    def test_mongodb_knowledge_base_survives_restart_and_deletion(self):
        from src.knowledge_base import MongoKnowledgeBase
        db = self.mongo.db
        session = MagicMock()
        session.__enter__.return_value.with_transaction.side_effect = lambda operation: operation(None)
        with patch.object(db.client, "start_session", return_value=session):
            kb = MongoKnowledgeBase(db, LexicalIndex)
            result = kb.add("approved.txt", b"Savings accounts support withdrawals.", "Accounts")
            restarted = MongoKnowledgeBase(db, LexicalIndex)
            self.assertEqual(restarted.list_documents()[0]["category"], "Accounts")
            self.assertTrue(restarted._chunks())
            restarted.delete(result["id"])
            self.assertEqual(restarted.list_documents(), [])
            self.assertEqual(restarted._chunks(), [])

    def test_webhook_secret_and_duplicate_update_persistence(self):
        database = self.mongo.db
        api.app.state.telegram_database = database
        api.app.state.telegram_application = object()
        client = TestClient(api.app)
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "test-secret"}):
            self.assertEqual(client.post("/telegram/webhook", json={"update_id": 123}).status_code, 403)
            headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
            for _ in range(2):
                self.assertEqual(client.post("/telegram/webhook", headers=headers, json={"update_id": 123}).status_code, 200)
            self.assertEqual(database.telegram_inbox.count_documents({}), 1)
            self.assertEqual(client.post("/telegram/webhook", headers=headers, json={}).status_code, 400)
        del api.app.state.telegram_database
        del api.app.state.telegram_application

    def test_lexical_mode_finds_evidence_without_embedding_dependencies(self):
        index = LexicalIndex()
        index.rebuild([{"id": "a::1", "text": "Savings deposits accept withdrawals."},
                       {"id": "b::1", "text": "Mobile banking supports transfers."}])
        self.assertEqual(index.query("withdrawals", top_k=1)[0]["id"], "a::1")
        self.assertEqual(index.query("astronomy"), [])


if __name__ == "__main__":
    unittest.main()
