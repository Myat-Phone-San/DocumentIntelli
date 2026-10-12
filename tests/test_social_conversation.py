import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.conversation import conversational_reply, fixed_reply
from src.knowledge_base import KnowledgeBase, MongoKnowledgeBase


class SocialConversationTests(unittest.TestCase):
    def test_fixed_replies_without_llm_or_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = KnowledgeBase(Path(directory) / "kb.sqlite3")
            with patch("src.llm_service.generate_answer") as generate:
                for message in ("mingalarpar", "မင်္ဂလာပါ။", "okay", "OK!", "thnank you",
                                "ကျေးဇူးပါ", "ကျေးဇူးအများကြီးတင်တယ်နော်",
                                "good morning", "afternoon", "good evening"):
                    with self.subTest(message=message):
                        self.assertTrue(kb.answer(message)["answer"])
                generate.assert_not_called()

    def test_mixed_questions_are_not_fixed_replies(self):
        for message in ("hello, what are the rates?", "okay explain loans",
                        "thank you, compare accounts", "ကျေးဇူးပါ ချေးငွေအကြောင်းပြောပါ"):
            self.assertIsNone(fixed_reply(message))

    def test_llm_social_reply_receives_history(self):
        history = [{"role": "user", "content": "hello"}]
        with patch("src.llm_service.generate_answer", return_value=json.dumps(
                {"kind": "social", "answer": "I'm here and ready to help!"})) as generate:
            self.assertEqual(conversational_reply("How are you doing?", history),
                             "I'm here and ready to help!")
            self.assertEqual(json.loads(generate.call_args.args[0])["history"], history)

    def test_uncertain_or_invalid_routing_uses_documents(self):
        for output in ('{"kind":"document"}', 'not json', '[]',
                       '{"kind":"social","answer":null}'):
            with patch("src.llm_service.generate_answer", return_value=output):
                self.assertIsNone(conversational_reply("what about that?"))

    def test_social_reply_does_not_load_knowledge_base(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = KnowledgeBase(Path(directory) / "kb.sqlite3")
            with patch.object(kb, "_chunks", side_effect=AssertionError("KB read")), \
                    patch("src.llm_service.generate_answer", return_value=
                          '{"kind":"social","answer":"Happy to chat!"}'):
                self.assertEqual(kb.answer("How is it going?")["answer"], "Happy to chat!")
        self.assertIs(MongoKnowledgeBase.answer, KnowledgeBase.answer)


if __name__ == "__main__":
    unittest.main()
