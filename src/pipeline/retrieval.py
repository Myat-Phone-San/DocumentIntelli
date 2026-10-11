from __future__ import annotations

from pathlib import Path
from typing import Any
from collections import Counter
import math
import re
from uuid import uuid4

import chromadb

from src.pipeline.embeddings import EmbeddingService
from src.settings import CHROMA_DIR


def search_terms(text: str) -> list[str]:
    text = re.sub(r"\bfd\b", "fixed deposit", text.lower())
    ignored = {"compare", "and", "the", "of", "for", "what", "is", "are", "bank", "banks",
               "aya", "kbz", "cb", "yoma", "uab", "mab", "a"}
    return [term.rstrip("s") for term in re.findall(r"[^\W_]+", text)
            if term not in ignored]


def lexical_ranking(query: str, records: list[dict[str, Any]]) -> list[str]:
    """BM25 keyword candidates complement semantic search for exact product names."""
    terms = set(search_terms(query))
    tokenized = [search_terms(record["text"] + " " + record["source"]) for record in records]
    if not tokenized or not terms:
        return []
    average = sum(map(len, tokenized)) / len(tokenized) or 1
    frequencies = Counter(term for tokens in tokenized for term in set(tokens))
    scores = []
    for record, tokens in zip(records, tokenized):
        counts = Counter(tokens)
        score = 0.0
        for term in terms:
            frequency = counts[term]
            inverse = math.log(1 + (len(records) - frequencies[term] + 0.5) /
                               (frequencies[term] + 0.5))
            score += inverse * frequency * 2.5 / (
                frequency + 1.5 * (0.25 + 0.75 * len(tokens) / average))
        if score:
            scores.append((score, record["id"]))
    return [identifier for _, identifier in sorted(scores, reverse=True)]


def include_neighbors(selected: list[str], by_id: dict[str, dict]) -> list[dict]:
    """Keep primary hits, then at most one adjacent chunk on either side of each."""
    identifiers = list(selected)
    seen = set(identifiers)
    for identifier in selected:
        path, separator, number = identifier.rpartition("::")
        if not separator or not number.isdigit():
            continue
        for position in (int(number) - 1, int(number) + 1):
            neighbor = f"{path}::{position}"
            # by_id contains only records satisfying the original metadata filters.
            if neighbor in by_id and neighbor not in seen:
                seen.add(neighbor)
                identifiers.append(neighbor)
    return [by_id[identifier] for identifier in identifiers if identifier in by_id]


def select_context(query: str, ranked: list[str], records: list[dict], top_k: int) -> list[dict]:
    """Prefer named products and retain complete, bounded product schedules."""
    by_id = {record["id"]: record for record in records}
    query_terms = set(search_terms(query))
    products: dict[str, list[dict]] = {}
    matches: dict[str, int] = {}
    for record in records:
        path = record.get("path") or record["id"].rpartition("::")[0]
        products.setdefault(path, []).append(record)
        title_terms = set(search_terms(Path(record["source"]).stem)) - {"account"}
        if len(title_terms) >= 2 and title_terms <= query_terms:
            matches[path] = len(title_terms)

    # A named variant takes precedence over its generic product name.
    best_match = max(matches.values(), default=0)
    positions = {identifier: position for position, identifier in enumerate(ranked)}
    priority = []
    for path, specificity in matches.items():
        if specificity == best_match:
            primary = min(products[path], key=lambda row: positions.get(row["id"], len(ranked)))
            priority.append(primary["id"])
    priority.sort(key=lambda identifier: positions.get(identifier, len(ranked)))
    selected = list(dict.fromkeys(priority + ranked))[:top_k]
    context = include_neighbors(selected, by_id)
    seen = {row["id"] for row in context}
    for identifier in selected:
        if identifier not in priority:
            continue
        record = by_id[identifier]
        path = record.get("path") or identifier.rpartition("::")[0]
        siblings = products[path]
        # For long documents, expand around relevant sections rather than
        # discarding everything beyond the initial hit and its neighbors.
        if sum(len(row["text"]) for row in siblings) > 12000:
            sibling_ids = {row["id"] for row in siblings}
            candidates = list(dict.fromkeys(
                [item for item in ranked if item in sibling_ids]
                + lexical_ranking(query, [dict(row, source="") for row in siblings])
            ))
            used = sum(len(row["text"]) for row in context
                       if row["id"] in sibling_ids)
            for row in include_neighbors(candidates[:max(top_k, 3)], by_id):
                if row["id"] not in seen and used + len(row["text"]) <= 12000:
                    context.append(row)
                    seen.add(row["id"])
                    used += len(row["text"])
            continue
        for row in sorted(siblings, key=lambda row: int(row["id"].rpartition("::")[2])):
            if row["id"] not in seen:
                context.append(row)
                seen.add(row["id"])
    return context


class ChromaIndex:
    def __init__(self, persist_directory: str | Path | None = CHROMA_DIR) -> None:
        self.persist_directory = str(persist_directory)
        self.client = (chromadb.EphemeralClient() if persist_directory is None
                       else chromadb.PersistentClient(path=self.persist_directory))
        self.collection_name = (f"uploads_{uuid4().hex}" if persist_directory is None
                                else "banking_market_intelligence")
        self.collection = self.client.get_or_create_collection(name=self.collection_name)
        self.embedder = EmbeddingService()

    def rebuild(self, chunks: list[dict[str, Any]]) -> None:
        try:
            self.client.delete_collection(name=self.collection_name)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(name=self.collection_name)
        if not chunks:
            return

        texts = [chunk["text"] for chunk in chunks]
        embeddings = self.embedder.embed(texts)

        ids = []
        metadatas = []
        documents = []
        for idx, chunk in enumerate(chunks):
            ids.append(chunk["id"])
            metadatas.append(
                {
                    "source": chunk["source"],
                    "bank": chunk["bank"],
                    "category": chunk["category"],
                    "language": chunk["language"],
                    "path": chunk["path"],
                }
            )
            documents.append(chunk["text"])

        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents,
        )

    def query(
        self,
        query_text: str,
        bank: str = "Any",
        category: str = "Any",
        language: str = "Any",
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []
        where_clause: dict[str, Any] = {}
        if bank != "Any":
            where_clause["bank"] = {"$eq": bank}
        if category != "Any":
            where_clause["category"] = {"$eq": category}
        if language != "Any":
            where_clause["language"] = {"$eq": language}

        query_embedding = self.embedder.embed_one(query_text)
        # Another index instance may have rebuilt the collection with a new ID.
        self.collection = self.client.get_collection(name=self.collection_name)
        where = ({"$and": [{key: value} for key, value in where_clause.items()]}
                 if len(where_clause) > 1 else where_clause or None)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=max(top_k * 4, 20),
            where=where,
        )

        matched = []
        semantic_ids = (results or {}).get("ids") or [[]]
        for idx in range(len(semantic_ids[0])):
            matched.append(
                {
                    "id": results["ids"][0][idx],
                    "text": results["documents"][0][idx],
                    "source": results["metadatas"][0][idx].get("source"),
                    "path": results["metadatas"][0][idx].get("path"),
                    "bank": results["metadatas"][0][idx].get("bank"),
                    "category": results["metadatas"][0][idx].get("category"),
                    "language": results["metadatas"][0][idx].get("language"),
                }
            )

        # The local corpus is small: search all filtered text, including chunks
        # missed by the embedding model, then fuse the two independent rankings.
        stored = self.collection.get(where=where, include=["documents", "metadatas"])
        records = [dict(metadata, id=identifier, text=document)
                   for identifier, document, metadata in zip(
                       stored["ids"], stored["documents"], stored["metadatas"])]
        keywords = lexical_ranking(query_text, records)[:max(top_k * 4, 20)]
        scores: dict[str, float] = {}
        for ranking in ([record["id"] for record in matched], keywords):
            for position, identifier in enumerate(ranking, start=1):
                scores[identifier] = scores.get(identifier, 0) + 1 / (60 + position)
        ranked = sorted(scores, key=scores.get, reverse=True)
        return select_context(query_text, ranked, records, top_k)
   
  
