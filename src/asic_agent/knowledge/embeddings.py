"""Embeddings for the knowledge base, through the configured API provider.

There is no local model. `get_embedder()` returns an ApiEmbedder for the
provider/model in config/llm.toml [embeddings] (or $EMBEDDING_PROVIDER /
$EMBEDDING_MODEL), or None when none is configured — retrieval is then
keyword-only and says so in the index metadata and every search response.

Vectors are L2-normalised, so cosine similarity is a dot product. The index
records the embedder's name ("<provider>:<model>"); queries must use the same
one, so changing the embedding model requires a rebuild (the retriever checks).
"""

from __future__ import annotations

import numpy as np

from ..llm import EmbeddingClient, get_embedding_client


def _l2_normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


class ApiEmbedder:
    needs_fit = False

    def __init__(self, client: EmbeddingClient):
        self.client = client
        self.name = f"{client.provider}:{client.model}"

    def fit(self, corpus: list[str]) -> None:
        return None

    def encode(self, texts: list[str]) -> np.ndarray:
        return _l2_normalise(np.asarray(self.client.embed(texts), dtype=np.float32))


def get_embedder() -> ApiEmbedder | None:
    client = get_embedding_client()
    return ApiEmbedder(client) if client else None
