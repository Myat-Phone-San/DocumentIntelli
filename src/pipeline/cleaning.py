from __future__ import annotations

import re


def clean_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n +", "\n", text)
    return text.strip()


def clean_documents(documents: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for doc in documents:
        doc_copy = dict(doc)
        doc_copy["text"] = clean_text(doc.get("text", ""))
        cleaned.append(doc_copy)
    return cleaned
