"""
Vector store.

Deliberately a plain numpy matrix rather than Chroma or FAISS. At this corpus
size — a few hundred to a few thousand chunks for one IP block — brute-force
cosine similarity is exact, runs in well under a millisecond, adds no
dependency, and can be inspected directly. Chroma and FAISS earn their keep at
100k+ vectors with approximate search; here they would add moving parts without
improving a single retrieval result.

If the corpus later grows past roughly 50k chunks, swapping this for FAISS is a
change to this file alone: `retriever.py` only calls `search()`.

Persisted as:
  index/vectors.npy   float32 [n_chunks, dim], L2-normalised
  index/chunks.jsonl  one chunk record per line, same order as the rows above
  index/meta.json     embedder name and build parameters
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class StoredChunk:
    chunk_id: str
    text: str
    source_path: str
    kind: str
    label: str
    line_start: int
    line_end: int

    @classmethod
    def from_dict(cls, d: dict) -> "StoredChunk":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


class VectorStore:
    def __init__(self, vectors: np.ndarray, chunks: list[StoredChunk], meta: dict):
        if len(vectors) != len(chunks):
            raise ValueError(
                f"vector/chunk count mismatch: {len(vectors)} vs {len(chunks)}"
            )
        self.vectors = vectors
        self.chunks = chunks
        self.meta = meta
        self._by_id = {c.chunk_id: i for i, c in enumerate(chunks)}

    # -- persistence -------------------------------------------------------

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self.vectors)
        with (directory / "chunks.jsonl").open("w", encoding="utf-8") as fh:
            for c in self.chunks:
                fh.write(json.dumps(c.__dict__) + "\n")
        (directory / "meta.json").write_text(
            json.dumps(self.meta, indent=1), encoding="utf-8"
        )

    @classmethod
    def load(cls, directory: Path) -> "VectorStore":
        vectors = np.load(directory / "vectors.npy")
        chunks = [
            StoredChunk.from_dict(json.loads(line))
            for line in (directory / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        return cls(vectors, chunks, meta)

    # -- search ------------------------------------------------------------

    def search(self, query_vec: np.ndarray, limit: int = 10) -> list[tuple[str, float]]:
        """Cosine similarity against every chunk. Vectors are pre-normalised,
        so the dot product is the cosine."""
        if self.vectors.size == 0:
            return []
        sims = self.vectors @ query_vec.reshape(-1)
        n = min(limit, len(sims))
        top = np.argpartition(-sims, n - 1)[:n]
        top = top[np.argsort(-sims[top])]
        return [(self.chunks[i].chunk_id, float(sims[i])) for i in top]

    def get(self, chunk_id: str) -> StoredChunk | None:
        i = self._by_id.get(chunk_id)
        return self.chunks[i] if i is not None else None
