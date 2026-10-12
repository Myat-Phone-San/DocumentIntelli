from __future__ import annotations

from typing import Any
import re

from src.llm_service import generate_answer
from src.pipeline.retrieval import ChromaIndex
from src.prompts import SYSTEM_PROMPT, DOCUMENT_SYSTEM_PROMPT
from src.settings import SUPPORTED_BANKS


DEFAULT_TOP_K = 5


class BankingRAG:
    def __init__(self, index: ChromaIndex, uploaded_documents: bool = False):
        self.index = index
        self.uploaded_documents = uploaded_documents

    def answer(
        self,
        query: str,
    ) -> dict[str, Any]:
        from src.conversation import conversational_reply
        reply = conversational_reply(query)
        if reply is not None:
            return {"answer": reply, "sources": []}
        mentioned_banks = [
            name for name in SUPPORTED_BANKS if (
                re.search(r"\ba\s+bank\b", query, re.IGNORECASE) if name == "A"
                else re.search(rf"\b{re.escape(name)}\b", query, re.IGNORECASE)
            )
        ]
        if self.uploaded_documents:
            mentioned_banks = []
        if len(mentioned_banks) > 1:
            # Reserve evidence for each named bank, even when one dominates similarity.
            sources = []
            for name in mentioned_banks:
                sources.extend(self.index.query(
                    query_text=query, bank=name, top_k=DEFAULT_TOP_K,
                ))
        else:
            sources = self.index.query(
                query_text=query,
                bank=mentioned_banks[0] if len(mentioned_banks) == 1 else "Any",
                top_k=DEFAULT_TOP_K,
            )

        if not sources:
            return {
                "answer": "I couldn't find relevant information in the selected documents for that question.",
                "sources": [],
            }

        context = "\n\n".join(
            f"Source: {source['source']}\nBank: {source['bank']}\nCategory: {source['category']}\nLanguage: {source['language']}\nText: {source['text']}"
            for source in sources
        )

        answer = generate_answer(
            query=query,
            context=context,
            system_prompt=DOCUMENT_SYSTEM_PROMPT if self.uploaded_documents else SYSTEM_PROMPT,
        )

        return {
            "answer": answer,
            "sources": sources,
        }
