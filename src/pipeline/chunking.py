from __future__ import annotations

from pathlib import Path
from typing import Any
import re
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.pipeline.cleaning import clean_documents


def chunk_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned_documents = clean_documents(documents)
    chunks: list[dict[str, Any]] = []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200, chunk_overlap=200,
        separators=["\n\n", "\n", "။", ". ", " ", ""],
    )

    for doc in cleaned_documents:
        text = doc.get("text", "")
        if not text:
            continue
        

        # Catalog entries must not mix one product's question with another's answer.
        sections = re.split(r"\n+(?=Category:)", text)
        paragraphs = []
        for section in sections:
            headings = re.findall(r"^(?:Product|Topic):[^\n]+", section, re.MULTILINE)
            prefix = "\n".join(headings) if len(sections) > 1 else ""
            if prefix and len(section) > 1200:
                section_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=max(400, 1200 - len(prefix) - 2), chunk_overlap=200,
                    separators=["\n\n", "\n", "။", ". ", " ", ""],
                )
                paragraphs.extend(prefix + "\n\n" + part
                                  for part in section_splitter.split_text(section))
            else:
                paragraphs.extend(splitter.split_text(section))
        for index, paragraph in enumerate(paragraphs, start=1):
            chunk_id = f"{Path(doc['path']).as_posix()}::{index}"
            chunks.append(
                {
                    "id": chunk_id,
                    "source": doc["source"],
                    "path": doc["path"],
                    "bank": doc.get("bank", "Unknown"),
                    "category": doc.get("category", "Other Services"),
                    "language": doc.get("language", "bilingual"),
                    "text": paragraph,
                }
            )

    return chunks



