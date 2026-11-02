"""Text embeddings via fastembed (ONNX Runtime; no PyTorch).

The model (BAAI/bge-small-en-v1.5, 384 dims, ~64 MB) is loaded lazily, once,
behind a lock, and inference runs in a worker thread so the event loop never
blocks. Tests and the simulation harness swap in `HashingEmbedder`, which is
deterministic and needs no download.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from pathlib import Path
from typing import Any, Protocol

import anyio

from app.core.config import BASE_DIR, settings
from app.core.logging import get_logger

log = get_logger(__name__)


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    def __init__(self, model_name: str, cache_dir: Path, threads: int, batch_size: int, dim: int) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.threads = threads
        self.batch_size = batch_size
        self.dim = dim
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    log.info("loading embedding model %s (first use)", self.model_name)
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    self._model = TextEmbedding(
                        self.model_name, cache_dir=str(self.cache_dir), threads=self.threads
                    )
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        return [vec.tolist() for vec in model.embed(texts, batch_size=self.batch_size)]

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        return next(iter(model.query_embed([text]))).tolist()


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedder (tests / offline simulation only)."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.md5(token.encode(), usedforsecurity=False).digest()
            vec[int.from_bytes(digest[:4], "little") % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        cache = Path(settings.embedding_cache_dir)
        _embedder = FastEmbedEmbedder(
            settings.embedding_model,
            cache if cache.is_absolute() else BASE_DIR / cache,
            settings.embedding_threads,
            settings.embedding_batch_size,
            settings.embedding_dim,
        )
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    global _embedder
    _embedder = embedder


async def embed_documents(texts: list[str]) -> list[list[float]]:
    return await anyio.to_thread.run_sync(get_embedder().embed_documents, texts)


async def embed_query(text: str) -> list[float]:
    return await anyio.to_thread.run_sync(get_embedder().embed_query, text)
