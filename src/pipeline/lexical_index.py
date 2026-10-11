"""Small-corpus BM25 index for low-memory hosts; no embedding model download."""
from collections import Counter
import math
import re
from types import SimpleNamespace


def terms(text):
    return re.findall(r"[^\W_]+", text.lower())


class LexicalIndex:
    def __init__(self):
        self.client = SimpleNamespace(delete_collection=lambda **kwargs: None)
        self.collection_name = "lexical"
        self.records = []

    def rebuild(self, chunks):
        self.records = chunks
        self.tokens = [Counter(terms(row["text"])) for row in chunks]
        self.lengths = [sum(tokens.values()) for tokens in self.tokens]
        self.average = sum(self.lengths) / len(self.lengths) if self.lengths else 1
        self.frequency = Counter(token for tokens in self.tokens for token in tokens)

    def query(self, query_text, top_k=5, **kwargs):
        query = set(terms(query_text)) - {"the", "a", "is", "what", "and", "of", "to", "in"}
        scores = []
        for row, tokens, length in zip(self.records, self.tokens, self.lengths):
            score = 0
            for token in query:
                count = tokens[token]
                inverse = math.log(1 + (len(self.records) - self.frequency[token] + .5) /
                                   (self.frequency[token] + .5))
                score += inverse * count * 2.5 / (count + 1.5 * (.25 + .75 * length / (self.average or 1)))
            if score:
                scores.append((score, row))
        selected = [row for _, row in sorted(scores, key=lambda item: item[0], reverse=True)[:top_k]]
        by_id = {row["id"]: row for row in self.records}
        result = {row["id"]: row for row in selected}
        for row in selected:
            path, _, position = row["id"].rpartition("::")
            if position.isdigit():
                for number in (int(position) - 1, int(position) + 1):
                    identifier = f"{path}::{number}"
                    if identifier in by_id:
                        result.setdefault(identifier, by_id[identifier])
        return list(result.values())
