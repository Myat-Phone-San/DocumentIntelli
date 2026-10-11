from __future__ import annotations

from typing import Any

from sentence_transformers import SentenceTransformer


EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingService:
    def __init__(self, model_name: str = EMBEDDING_MODEL) -> None:
        self.model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, convert_to_numpy=True, normalize_embeddings=True).tolist()

    def embed_one(self, text: str) -> list[float]:
        return self.model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0].tolist()
