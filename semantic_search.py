"""Small, optional semantic ranker with a no-network fallback in the caller."""
import hashlib
import json
import math
import os
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SemanticRanker:
    """Cache compact OpenAI embeddings and return cosine similarities by item ID."""

    def __init__(self, api_key=None, model=None, dimensions=None, timeout=None, opener=None):
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        self.dimensions = int(dimensions or os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "256"))
        self.timeout = float(timeout or os.getenv("OPENAI_EMBEDDING_TIMEOUT_SECONDS", "4"))
        self.opener = opener or urlopen
        self._cache = {}
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return bool(self.api_key)

    @staticmethod
    def _cosine(left, right):
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return dot / (left_norm * right_norm)

    def _embed(self, inputs):
        payload = json.dumps({
            "model": self.model,
            "input": inputs,
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }).encode()
        request = Request(
            "https://api.openai.com/v1/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError):
            return None
        data = sorted(body.get("data", []), key=lambda row: row.get("index", -1))
        vectors = [row.get("embedding") for row in data]
        if len(vectors) != len(inputs) or any(not isinstance(vector, list) for vector in vectors):
            return None
        return vectors

    def rank(self, query, item_texts):
        """Return {item_id: similarity}, or None when disabled or temporarily unavailable."""
        if not self.enabled or not query.strip() or not item_texts:
            return None
        cached_vectors = {}
        missing = []
        for item_id, item_text in item_texts.items():
            digest = hashlib.sha256(
                f"{self.model}:{self.dimensions}:{item_text}".encode()
            ).hexdigest()
            with self._lock:
                vector = self._cache.get(digest)
            if vector is None:
                missing.append((item_id, item_text, digest))
            else:
                cached_vectors[item_id] = vector

        request_inputs = [query] + [item_text for _, item_text, _ in missing]
        vectors = self._embed(request_inputs)
        if vectors is None:
            return None
        query_vector = vectors[0]
        for (item_id, _, digest), vector in zip(missing, vectors[1:]):
            cached_vectors[item_id] = vector
            with self._lock:
                self._cache[digest] = vector
        return {item_id: self._cosine(query_vector, vector)
                for item_id, vector in cached_vectors.items()}
