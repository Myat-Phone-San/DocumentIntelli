import json
import hashlib
import hmac
import os
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import mongomock
from fastapi.testclient import TestClient

import api
from src import messenger
from src.mongo_store import now


ENV = {"MESSENGER_ENABLED": "true", "MESSENGER_APP_SECRET": "test-secret",
       "MESSENGER_VERIFY_TOKEN": "verify", "MESSENGER_APP_ID": "99",
       "MESSENGER_PAGE_ID": "100", "ADMIN_API_KEY": "admin"}


def event(text="hello", mid="m1", echo=False, app_id=None):
    message = {"text": text, "mid": mid}
    if echo:
        message["is_echo"] = True
        if app_id:
            message["app_id"] = app_id
    return {"sender": {"id": "100" if echo else "200"},
            "recipient": {"id": "200" if echo else "100"},
            "timestamp": int(now().timestamp() * 1000), "message": message}


def payload(*events):
    return {"object": "page", "entry": [{"id": "100", "messaging": list(events)}]}


class MessengerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, ENV)
        self.environment.start()
        self.db = mongomock.MongoClient(tz_aware=True).test
        api.app.state.messenger_db = self.db
        self.client = TestClient(api.app)

    def tearDown(self):
        del api.app.state.messenger_db
        self.environment.stop()
        self.client.close()

    def test_verification_and_signature(self):
        response = self.client.get('/messenger/webhook', params={
            'hub.mode': 'subscribe', 'hub.verify_token': 'verify', 'hub.challenge': '123'})
        self.assertEqual(response.text, '123')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/messenger/webhook', params={
            'hub.mode': 'subscribe', 'hub.verify_token': 'wrong', 'hub.challenge': '123'}).status_code, 403)
        body = json.dumps(payload(event())).encode()
        self.assertEqual(self.client.post('/messenger/webhook', content=body).status_code, 403)
        signature = 'sha256=' + hmac.new(b'test-secret', body, hashlib.sha256).hexdigest()
        response = self.client.post('/messenger/webhook', content=body,
                                    headers={'x-hub-signature-256': signature})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.messenger_inbox.count_documents({}), 1)

    def test_duplicates_echoes_and_human_request(self):
        messenger.ingest(self.db, payload(event()))
        messenger.ingest(self.db, payload(event()))
        self.assertEqual(self.db.messenger_inbox.count_documents({}), 1)
        messenger.ingest(self.db, payload(event(mid='own', echo=True, app_id='99')))
        self.assertEqual(self.db.messenger_threads.find_one({'_id': '200'})['mode'], 'ai')
        messenger.ingest(self.db, payload(event(mid='manual', echo=True)))
        self.assertEqual(self.db.messenger_threads.find_one({'_id': '200'})['mode'], 'human')
        messenger.set_mode(self.db, '200', 'ai')
        messenger.ingest(self.db, payload(event(text='human', mid='human')))
        self.assertTrue(self.db.messenger_inbox.find_one({'_id': 'human'})['human_request'])
        self.assertEqual(self.db.messenger_threads.find_one({'_id': '200'})['mode'], 'human')

    def test_admin_routes_require_key(self):
        self.assertEqual(self.client.get('/admin/messenger/threads').status_code, 403)
        self.assertEqual(self.client.post('/admin/messenger/threads/200/mode', json={'mode': 'ai'}).status_code, 403)
        response = self.client.post('/admin/messenger/threads/200/mode', json={'mode': 'human'},
                                    headers={'X-Admin-Key': 'admin'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['mode'], 'human')

    async def test_ai_history_and_manual_takeover_during_generation(self):
        messenger.ingest(self.db, payload(event()))
        item = messenger.claim(self.db)
        kb = SimpleNamespace(answer=lambda *args: {'answer': 'Hello!'})
        with patch.object(api, 'get_knowledge_base', return_value=kb), patch.object(messenger, 'send', new_callable=AsyncMock) as send:
            await messenger.handle(self.db, item)
            send.assert_awaited_once_with('200', 'Hello!')
        self.assertEqual(self.db.messenger_history.count_documents({}), 1)
        messenger.ingest(self.db, payload(event(mid='second')))
        item = messenger.claim(self.db)
        # Complete first work item so the next claim tests the second message.
        self.db.messenger_inbox.update_one({'_id': 'm1'}, {'$set': {'state': 'done'}})
        def takeover(*args):
            messenger.set_mode(self.db, '200', 'human')
            return {'answer': 'Should not be sent'}
        with patch.object(api, 'get_knowledge_base', return_value=SimpleNamespace(answer=takeover)), patch.object(messenger, 'send', new_callable=AsyncMock) as send:
            await messenger.handle(self.db, item)
            send.assert_not_awaited()

    async def test_human_mode_and_expired_window_do_not_call_ai(self):
        messenger.ingest(self.db, payload(event()))
        item = messenger.claim(self.db)
        for mode, contact in [('human', now()), ('ai', now() - timedelta(hours=25))]:
            self.db.messenger_threads.update_one({'_id': '200'}, {'$set': {'mode': mode, 'last_contact': contact}})
            with patch.object(api, 'get_knowledge_base') as kb, patch.object(messenger, 'send', new_callable=AsyncMock) as send:
                await messenger.handle(self.db, item)
                kb.assert_not_called()
                send.assert_not_awaited()

    async def test_non_admin_telegram_cannot_control_messenger(self):
        import telegram_bot
        update = SimpleNamespace(effective_user=SimpleNamespace(id=22),
            effective_chat=SimpleNamespace(type='private'),
            effective_message=SimpleNamespace(reply_text=AsyncMock()))
        with patch.object(telegram_bot, 'ADMIN_IDS', {11}), patch.object(telegram_bot, 'api_call', new_callable=AsyncMock) as backend:
            await telegram_bot.messenger_admin(update, SimpleNamespace(args=['200', 'ai']))
            backend.assert_not_awaited()

    async def test_pause_resume_skips_old_queue_and_human_request_acknowledges(self):
        messenger.ingest(self.db, payload(event()))
        item = messenger.claim(self.db)
        messenger.set_mode(self.db, '200', 'human')
        messenger.set_mode(self.db, '200', 'ai')
        with patch.object(api, 'get_knowledge_base') as kb, patch.object(messenger, 'send', new_callable=AsyncMock) as send:
            await messenger.handle(self.db, item)
            kb.assert_not_called()
            send.assert_not_awaited()
        messenger.ingest(self.db, payload(event(text='human support', mid='support')))
        item = messenger.claim(self.db)
        with patch.object(messenger, 'send', new_callable=AsyncMock) as send:
            await messenger.handle(self.db, item)
            self.assertIn('AI replies are paused', send.call_args.args[1])
            self.assertFalse(send.call_args.args[2])


if __name__ == '__main__':
    unittest.main()
